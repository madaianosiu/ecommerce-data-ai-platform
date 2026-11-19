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
