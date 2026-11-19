from datetime import date, datetime
from decimal import Decimal

import psycopg
import pytest

from app import queries


def test_revenue_shape_and_money_as_numbers(client, auth_headers, monkeypatch):
    monkeypatch.setattr(queries, "revenue", lambda *a: [
        {"period": None, "revenue": Decimal("300.50"), "orders": 3, "items": 4},
        {"period": date(2017, 1, 1), "revenue": Decimal("100.25"), "orders": 1, "items": 1},
        {"period": date(2017, 2, 1), "revenue": Decimal("200.25"), "orders": 2, "items": 3},
    ])
    r = client.get("/v1/metrics/revenue", params={"from": "2017-01-01", "to": "2017-02-28"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"currency", "granularity", "from", "to", "total_revenue", "orders", "items", "series"}
    assert body["total_revenue"] == 300.50 and isinstance(body["total_revenue"], float)
    assert body["from"] == "2017-01-01" and body["currency"] == "BRL" and body["granularity"] == "month"
    assert body["series"] == [
        {"period": "2017-01-01", "revenue": 100.25, "orders": 1, "items": 1},
        {"period": "2017-02-01", "revenue": 200.25, "orders": 2, "items": 3},
    ]


def test_revenue_empty_range_is_zero(client, auth_headers, monkeypatch):
    monkeypatch.setattr(queries, "revenue", lambda *a: [
        {"period": None, "revenue": None, "orders": 0, "items": 0}])
    body = client.get("/v1/metrics/revenue", headers=auth_headers).json()
    assert body["total_revenue"] == 0 and body["series"] == []


def test_delivery_shape(client, auth_headers, monkeypatch):
    monkeypatch.setattr(queries, "delivery_performance", lambda *a: {
        "orders": 10, "avg_delivery_days": 8.5, "late_orders": 1, "late_pct": 10.0,
        "excluded_missing_delivery_date": 2})
    body = client.get("/v1/metrics/delivery-performance", headers=auth_headers).json()
    assert body == {"state": None, "orders": 10, "avg_delivery_days": 8.5, "late_orders": 1,
                    "late_pct": 10.0, "excluded_missing_delivery_date": 2}


def test_top_products_shape(client, auth_headers, monkeypatch):
    monkeypatch.setattr(queries, "top_products", lambda *a: [
        {"product_id": "p1", "product_category": "toys", "quantity": 5,
         "revenue_brl": Decimal("50.00"), "revenue_eur": Decimal("13.10")}])
    body = client.get("/v1/products/top?limit=1&metric=quantity", headers=auth_headers).json()
    assert body == {"metric": "quantity", "category": None, "limit": 1, "items": [
        {"product_id": "p1", "product_category": "toys", "quantity": 5,
         "revenue_brl": 50.0, "revenue_eur": 13.1}]}


def test_orders_shape(client, auth_headers, monkeypatch):
    row = {"order_id": "o1", "customer_unique_id": "c1", "order_status": "delivered",
           "order_purchase_at": datetime(2018, 1, 1, 10, 0), "order_approved_at": None,
           "order_delivered_customer_at": datetime(2018, 1, 5, 9, 0),
           "order_estimated_delivery_at": datetime(2018, 1, 10), "delivery_days": 4, "is_late": False}
    monkeypatch.setattr(queries, "orders_page", lambda *a: [row])
    body = client.get("/v1/orders", headers=auth_headers).json()
    assert body["limit"] == 50 and body["next_cursor"] is None
    assert body["items"][0]["order_purchase_at"] == "2018-01-01T10:00:00"
    assert set(body["items"][0]) == set(row)


@pytest.mark.parametrize("path, fn", [
    ("/v1/metrics/revenue", "revenue"),
    ("/v1/metrics/delivery-performance", "delivery_performance"),
    ("/v1/products/top", "top_products"),
    ("/v1/orders", "orders_page"),
])
def test_database_errors_are_503_without_internal_details(client, auth_headers, monkeypatch, path, fn):
    def boom(*a):
        raise psycopg.OperationalError('connection to server at "10.0.0.5" failed: password for user "api_reader"')
    monkeypatch.setattr(queries, fn, boom)
    r = client.get(path, headers=auth_headers)
    assert r.status_code == 503
    assert r.json() == {"detail": "Database unavailable"}
    assert "10.0.0.5" not in r.text and "api_reader" not in r.text
