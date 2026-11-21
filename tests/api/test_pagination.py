"""Keyset pagination without a database: a fake orders_page applies the same rule as the SQL
((order_purchase_at, order_id) < cursor, sorted descending), on in-memory data."""
from datetime import datetime, timedelta

import pytest

from app import queries
from app.pagination import InvalidCursor, decode_cursor, encode_cursor

T0 = datetime(2018, 1, 1, 12, 0, 0)
# 11 orders; some share a timestamp, to test the order_id tie-break.
ROWS = [
    {"order_id": f"o{i:02d}", "customer_unique_id": f"c{i}", "order_status": "delivered",
     "order_purchase_at": T0 - timedelta(hours=i // 3), "order_approved_at": None,
     "order_delivered_customer_at": None, "order_estimated_delivery_at": None,
     "delivery_days": None, "is_late": None}
    for i in range(11)
]


def fake_orders_page(conn, status, after, limit):
    ordered = sorted(ROWS, key=lambda r: (r["order_purchase_at"], r["order_id"]), reverse=True)
    if after:
        ordered = [r for r in ordered if (r["order_purchase_at"], r["order_id"]) < after]
    return ordered[: limit + 1]


@pytest.fixture(autouse=True)
def fake_queries(monkeypatch):
    monkeypatch.setattr(queries, "orders_page", fake_orders_page)


def _all_pages(client, headers, limit):
    pages, cursor = [], None
    while True:
        params = {"limit": limit, **({"cursor": cursor} if cursor else {})}
        body = client.get("/v1/orders", params=params, headers=headers).json()
        pages.append([o["order_id"] for o in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


def test_page_two_does_not_repeat_page_one(client, auth_headers):
    pages = _all_pages(client, auth_headers, limit=3)
    assert not set(pages[0]) & set(pages[1])


def test_pages_cover_everything_exactly_once(client, auth_headers):
    pages = _all_pages(client, auth_headers, limit=3)
    flat = [oid for page in pages for oid in page]
    assert [len(p) for p in pages] == [3, 3, 3, 2]
    assert len(flat) == len(set(flat)) == len(ROWS)


def test_last_page_has_null_cursor_when_exact_multiple(client, auth_headers):
    pages = _all_pages(client, auth_headers, limit=11)
    assert pages == [[r["order_id"] for r in fake_orders_page(None, None, None, 11)][:11]]


def test_cursor_roundtrip():
    assert decode_cursor(encode_cursor(T0, "abc")) == (T0, "abc")


@pytest.mark.parametrize("bad", ["", "%%%", "eyJ0IjoiMjAxOCJ9", "eyJ0IjoiMjAxOC0wMS0wMSIsImlkIjoiIn0"])
def test_decode_rejects_garbage(bad):
    with pytest.raises(InvalidCursor):
        decode_cursor(bad)
