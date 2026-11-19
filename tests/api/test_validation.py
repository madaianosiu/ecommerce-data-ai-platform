import pytest

from app import queries


@pytest.fixture(autouse=True)
def fake_queries(monkeypatch):
    calls = {}

    def record(name, result):
        def fake(*args):
            calls[name] = args
            return result
        return fake

    monkeypatch.setattr(queries, "revenue", record("revenue", []))
    monkeypatch.setattr(queries, "delivery_performance", record("delivery", {
        "orders": 0, "avg_delivery_days": None, "late_orders": 0, "late_pct": None,
        "excluded_missing_delivery_date": 0}))
    monkeypatch.setattr(queries, "top_products", record("top", []))
    monkeypatch.setattr(queries, "orders_page", record("orders", []))
    return calls


def _errors(response) -> str:
    return str(response.json()["detail"])


@pytest.mark.parametrize("path, params, fragment", [
    ("/v1/metrics/revenue", {"from": "2018-02-01", "to": "2018-01-01"}, "'from' must be on or before 'to'"),
    ("/v1/metrics/revenue", {"from": "2018-13-01"}, "date"),
    ("/v1/metrics/revenue", {"granularity": "week"}, "day"),
    ("/v1/metrics/revenue", {"currency": "USD"}, "BRL"),
    ("/v1/metrics/revenue", {"granularty": "day"}, "Extra inputs are not permitted"),
    ("/v1/metrics/delivery-performance", {"state": "XX"}, "SP"),
    ("/v1/metrics/delivery-performance", {"state": "SPX"}, "SP"),
    ("/v1/products/top", {"limit": 0}, "greater than or equal to 1"),
    ("/v1/products/top", {"limit": 101}, "less than or equal to 100"),
    ("/v1/products/top", {"metric": "profit"}, "revenue"),
    ("/v1/products/top", {"category": "x'; drop table core.fact_orders; --"}, "pattern"),
    ("/v1/orders", {"limit": 201}, "less than or equal to 200"),
    ("/v1/orders", {"limit": "abc"}, "integer"),
    ("/v1/orders", {"status": "lost"}, "delivered"),
])
def test_invalid_params_are_422_with_clear_message(client, auth_headers, path, params, fragment):
    r = client.get(path, params=params, headers=auth_headers)
    assert r.status_code == 422, r.text
    assert fragment in _errors(r)


@pytest.mark.parametrize("cursor", ["not-base64!!", "e30", "eyJ0IjoiYmFkIiwiaWQiOiJ4In0"])
def test_invalid_cursor_is_400(client, auth_headers, cursor):
    r = client.get("/v1/orders", params={"cursor": cursor}, headers=auth_headers)
    assert r.status_code == 400
    assert "Invalid cursor" in r.json()["detail"]


def test_valid_params_reach_the_query_layer(client, auth_headers, fake_queries):
    r = client.get("/v1/metrics/revenue", headers=auth_headers,
                   params={"from": "2017-01-01", "to": "2017-01-01", "granularity": "day", "currency": "EUR"})
    assert r.status_code == 200
    _, date_from, date_to, granularity, currency = fake_queries["revenue"]
    assert (str(date_from), str(date_to), granularity.value, currency.value) == (
        "2017-01-01", "2017-01-01", "day", "EUR")


def test_state_is_case_insensitive(client, auth_headers, fake_queries):
    r = client.get("/v1/metrics/delivery-performance", params={"state": "sp"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["state"] == "SP"
    assert fake_queries["delivery"][1] == "SP"


def test_defaults(client, auth_headers, fake_queries):
    client.get("/v1/products/top", headers=auth_headers)
    client.get("/v1/orders", headers=auth_headers)
    assert fake_queries["top"][2:] == (10, queries.TopMetric.revenue)
    assert fake_queries["orders"][1:] == (None, None, 50)
