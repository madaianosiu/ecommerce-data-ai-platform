"""Fixture-uri pentru testele API.

- `client`: fără bază de date. Setările și conexiunea sunt înlocuite prin dependency_overrides,
  iar funcțiile din app.queries se înlocuiesc per test cu monkeypatch.
- `live_client`: aplicația reală (lifespan + pool) conectată ca api_reader. Se sare automat dacă
  variabilele de mediu lipsesc sau baza nu răspunde. Rulare: dotenv -f .env run -- pytest
"""
import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import get_conn
from app.main import app

TEST_API_KEY = "test-key-0123456789"


@pytest.fixture
def api_key() -> str:
    return TEST_API_KEY


@pytest.fixture
def auth_headers(api_key) -> dict:
    return {"X-API-Key": api_key}


@pytest.fixture
def client():
    app.dependency_overrides[get_settings] = lambda: Settings(api_key=TEST_API_KEY)
    app.dependency_overrides[get_conn] = lambda: object()  # conexiune falsă; queries sunt mock-uite
    try:
        yield TestClient(app)  # fără `with`: lifespan-ul (pool-ul) nu pornește
    finally:
        app.dependency_overrides.clear()


def _live_settings_or_skip() -> Settings:
    required = ("POSTGRES_DB", "API_DB_USER", "API_DB_PASSWORD", "API_KEY")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip(f"integration: missing env vars {missing} (run via dotenv)")
    get_settings.cache_clear()
    settings = get_settings()
    try:
        with psycopg.connect(settings.conninfo(), connect_timeout=3) as conn:
            conn.execute("select 1 from core.fact_orders limit 1")
    except psycopg.Error as exc:
        pytest.skip(f"integration: database not available ({type(exc).__name__})")
    return settings


@pytest.fixture(scope="session")
def live_settings() -> Settings:
    return _live_settings_or_skip()


@pytest.fixture(scope="session")
def live_client(live_settings):
    app.dependency_overrides.clear()
    with TestClient(app, headers={"X-API-Key": live_settings.api_key}) as c:
        yield c
