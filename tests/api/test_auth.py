import pytest

from app import queries
from app.config import Settings, get_settings
from app.main import app

PROTECTED = [
    "/v1/metrics/revenue",
    "/v1/metrics/delivery-performance",
    "/v1/products/top",
    "/v1/orders",
]


@pytest.fixture(autouse=True)
def fake_queries(monkeypatch):
    monkeypatch.setattr(queries, "revenue", lambda *a: [])
    monkeypatch.setattr(queries, "delivery_performance", lambda *a: {
        "orders": 0, "avg_delivery_days": None, "late_orders": 0, "late_pct": None,
        "excluded_missing_delivery_date": 0})
    monkeypatch.setattr(queries, "top_products", lambda *a: [])
    monkeypatch.setattr(queries, "orders_page", lambda *a: [])


@pytest.mark.parametrize("path", PROTECTED)
def test_missing_key_is_401(client, path):
    r = client.get(path)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "ApiKey"
    assert "X-API-Key" in r.json()["detail"]


@pytest.mark.parametrize("path", PROTECTED)
def test_wrong_key_is_401(client, path):
    assert client.get(path, headers={"X-API-Key": "wrong"}).status_code == 401


@pytest.mark.parametrize("path", PROTECTED)
def test_correct_key_is_200(client, auth_headers, path):
    assert client.get(path, headers=auth_headers).status_code == 200


def test_key_prefix_is_not_enough(client, api_key):
    assert client.get("/v1/orders", headers={"X-API-Key": api_key[:-1]}).status_code == 401


def test_server_without_configured_key_rejects_everyone(client):
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="")
    assert client.get("/v1/orders", headers={"X-API-Key": ""}).status_code == 401
    assert client.get("/v1/orders").status_code == 401


def test_auth_is_checked_before_validation(client):
    # Without a key, even invalid parameters get 401 (we reveal nothing about the API).
    assert client.get("/v1/orders?limit=9999").status_code == 401


def test_health_needs_no_key(client):
    # Without a pool (the lifespan does not run), health returns 503, but not 401.
    r = client.get("/v1/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "database": "unavailable"}


def test_openapi_declares_api_key_scheme(client):
    spec = client.get("/openapi.json").json()
    assert spec["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey", "in": "header", "name": "X-API-Key",
        "description": spec["components"]["securitySchemes"]["APIKeyHeader"]["description"],
    }
    assert "security" not in spec["paths"]["/v1/health"]["get"]
    assert spec["paths"]["/v1/orders"]["get"]["security"] == [{"APIKeyHeader": []}]
    assert client.get("/docs").status_code == 200
