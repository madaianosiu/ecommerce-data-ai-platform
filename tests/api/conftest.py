"""Fixtures for the API tests.

- `client`: no database. Settings and the connection are replaced through dependency_overrides,
  and the functions in app.queries are replaced per test with monkeypatch.
- `live_client`: the real app (lifespan + pool) connected as api_reader. Skipped automatically if
  the environment variables are missing or the database does not respond. Run: dotenv -f .env run -- pytest
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
    app.dependency_overrides[get_conn] = lambda: object()  # fake connection; queries are mocked
    try:
        yield TestClient(app)  # no `with`: the lifespan (pool) does not start
    finally:
        app.dependency_overrides.clear()


def _live_settings_or_skip() -> Settings:
    required = ("POSTGRES_DB", "API_DB_USER", "API_DB_PASSWORD", "API_KEY")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip(f"integration: missing env vars {missing} (run via dotenv)")
    # The integration tests don't need the embedding model (loading it takes tens of seconds).
    os.environ["API_PRELOAD_EMBEDDER"] = "0"
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
