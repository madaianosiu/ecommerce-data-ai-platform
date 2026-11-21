"""Teste pe baza reală, prin rolul api_reader. Se sar automat dacă baza nu e disponibilă.
Rulare: .venv\\Scripts\\dotenv -f .env run -- pytest -m integration"""
import psycopg
import pytest

pytestmark = pytest.mark.integration

# Cifre reconciliate în dbt (README, _core.yml): venit = sum(price) fără canceled/unavailable.
REVENUE_BRL = 13494400.74
REVENUE_EUR = 3424922.94


def test_health(live_client):
    r = live_client.get("/v1/health", headers={})
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


@pytest.mark.parametrize("currency, expected", [("BRL", REVENUE_BRL), ("EUR", REVENUE_EUR)])
def test_total_revenue_matches_dbt(live_client, currency, expected):
    body = live_client.get("/v1/metrics/revenue", params={"currency": currency, "granularity": "year"}).json()
    assert body["total_revenue"] == expected
    assert round(sum(p["revenue"] for p in body["series"]), 2) == expected
    assert [p["period"] for p in body["series"]] == ["2016-01-01", "2017-01-01", "2018-01-01"]


def test_revenue_filtered_range(live_client):
    body = live_client.get("/v1/metrics/revenue",
                           params={"from": "2017-01-01", "to": "2017-12-31", "granularity": "month"}).json()
    assert len(body["series"]) == 12
    assert round(sum(p["revenue"] for p in body["series"]), 2) == body["total_revenue"]
    assert sum(p["orders"] for p in body["series"]) == body["orders"]


def test_delivery_performance(live_client):
    overall = live_client.get("/v1/metrics/delivery-performance").json()
    sp = live_client.get("/v1/metrics/delivery-performance", params={"state": "SP"}).json()
    assert overall["state"] is None and sp["state"] == "SP"
    assert 0 < sp["orders"] < overall["orders"]
    assert 0 <= sp["late_pct"] <= 100 and sp["avg_delivery_days"] > 0
    assert overall["excluded_missing_delivery_date"] == 8  # cifra din dbt / README


def test_top_products_sorted(live_client):
    body = live_client.get("/v1/products/top", params={"limit": 10}).json()
    revenues = [p["revenue_brl"] for p in body["items"]]
    assert len(revenues) == 10 and revenues == sorted(revenues, reverse=True)
    by_qty = live_client.get("/v1/products/top", params={"metric": "quantity", "category": "unknown"}).json()
    assert all(p["product_category"] == "unknown" for p in by_qty["items"])
    qty = [p["quantity"] for p in by_qty["items"]]
    assert qty == sorted(qty, reverse=True)


def test_orders_pagination_has_no_overlap_and_is_ordered(live_client):
    seen, cursor, keys = set(), None, []
    for _ in range(3):
        params = {"limit": 200, "status": "delivered", **({"cursor": cursor} if cursor else {})}
        body = live_client.get("/v1/orders", params=params).json()
        ids = [o["order_id"] for o in body["items"]]
        assert len(ids) == 200 and not seen & set(ids)
        assert all(o["order_status"] == "delivered" for o in body["items"])
        seen.update(ids)
        keys += [(o["order_purchase_at"], o["order_id"]) for o in body["items"]]
        cursor = body["next_cursor"]
        assert cursor
    assert keys == sorted(keys, reverse=True)


def test_last_orders_page_has_null_cursor(live_client):
    body = live_client.get("/v1/orders", params={"status": "approved", "limit": 200}).json()
    assert len(body["items"]) == 2 and body["next_cursor"] is None


def test_api_role_cannot_read_raw_or_staging(live_settings):
    with psycopg.connect(live_settings.conninfo()) as conn:
        for table in ("raw.orders", "staging.stg_orders"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"select 1 from {table} limit 1")
            conn.rollback()


def test_api_role_cannot_write_core(live_settings):
    with psycopg.connect(live_settings.conninfo()) as conn:
        with pytest.raises((psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege)):
            conn.execute("delete from core.fact_orders where false")


def test_review_retrieval_as_api_reader(live_settings):
    """Retrieval pentru /v1/ask, fără model: vectorul-întrebare e un embedding deja stocat,
    deci recenzia lui trebuie să iasă prima, cu similaritate ~1."""
    from psycopg.rows import dict_row

    from app import queries
    from app.embedder import MODEL_NAME

    with psycopg.connect(live_settings.conninfo(), row_factory=dict_row, autocommit=True) as conn:
        probe = conn.execute(
            "select review_id, embedding::text as v from core.review_embeddings "
            "where model_name = %s and review_score = 1 order by review_id limit 1", (MODEL_NAME,)).fetchone()
        if probe is None:
            pytest.skip("integration: core.review_embeddings is empty (run src/rag/embed_reviews.py)")
        vector = [float(x) for x in probe["v"].strip("[]").split(",")]

        rows = queries.search_reviews(conn, vector, MODEL_NAME, None, None, limit=10)
        assert rows[0]["review_id"] == probe["review_id"] and rows[0]["similarity"] > 0.999
        sims = [r["similarity"] for r in rows]
        assert sims == sorted(sims, reverse=True)

        # 120 candidați cu filtru pe scor. Măsurat cu setările implicite (ef_search=40, fără iterative
        # scan): același query întoarce 40 de rânduri fără filtru și doar 2 cu filtrul 4-5.
        filtered = queries.search_reviews(conn, vector, MODEL_NAME, 4, 5, limit=120)
        assert len(filtered) == 120
        assert all(4 <= r["review_score"] <= 5 for r in filtered)


def test_sql_route_revenue_matches_metrics_endpoint(live_client, live_settings):
    """Ruta sql cap-coadă pe baza reală (validator + executor read-only ca api_reader), cu LLM-ul
    scriptat (fără rețea): venitul trebuie să fie identic cu /v1/metrics/revenue."""
    from contextlib import contextmanager

    from psycopg.rows import dict_row

    from app import text_to_sql
    from app.llm import LLMResult

    class ScriptedLLM:
        def __init__(self, *texts):
            self.texts = list(texts)

        def generate(self, system_prompt, user_prompt, *, temperature=None, json_output=False):
            return LLMResult(text=self.texts.pop(0), model="scripted")

    @contextmanager
    def api_reader_connection():
        with psycopg.connect(live_settings.conninfo(), row_factory=dict_row, autocommit=True) as conn:
            yield conn

    with api_reader_connection() as conn:
        ctx = text_to_sql.load_sql_context(conn)
    assert "review_embeddings" not in ctx.allowed_tables and "fact_order_items" in ctx.allowed_tables

    llm = ScriptedLLM("SELECT ROUND(SUM(price_brl), 2) AS revenue_brl FROM core.fact_order_items WHERE is_revenue",
                      "Total revenue was R$ 13,494,400.74.")
    outcome = text_to_sql.answer_with_sql("What is the total revenue?", llm, ctx, api_reader_connection,
                                          expected_user=live_settings.db_user)
    metrics = live_client.get("/v1/metrics/revenue", params={"granularity": "year"}).json()
    assert outcome.rows == [{"revenue_brl": REVENUE_BRL}]
    assert outcome.rows[0]["revenue_brl"] == metrics["total_revenue"] == 13494400.74
