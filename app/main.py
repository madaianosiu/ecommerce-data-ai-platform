"""Data API over schema core.

Local run (from the project root, with the variables from .env):
    .venv\\Scripts\\dotenv -f .env run -- uvicorn app.main:app --reload
"""
import logging
from contextlib import asynccontextmanager

import psycopg
from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from app.auth import require_api_key
from app.config import get_settings
from app.db import DatabaseUnavailable, create_pool
from app.embedder import E5Embedder
from app.llm import LLMNotConfigured, create_llm
from app.routers import ask, health, metrics, orders, products

log = logging.getLogger("app")

DESCRIPTION = """
Read-only API over the dimensional model in schema `core` (built with dbt from the Olist data).

* **Authentication:** `X-API-Key` header on every endpoint except `/v1/health`.
* **Revenue:** sum of `price` for orders that are not `canceled` / `unavailable`; EUR amounts
  use the BRL→EUR rate of the purchase day.
* **`/v1/ask`:** a router sends each question to read-only text-to-SQL (numbers) or to an LLM answer
  built only from written reviews found by semantic search, with [n] citations. Written reviews are
  a subset that skews toward low scores.
* **Errors:** 401 missing/wrong key, 400/422 invalid parameters, 503 database or LLM temporarily
  unavailable, 502 LLM error.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    pool = create_pool(settings)
    pool.open(wait=False)  # don't block startup; /v1/health reports whether the database responds
    app.state.pool = pool

    # /v1/ask: the LLM client (no network call) and the embedding model, loaded once.
    # If either is missing, the other endpoints work normally; /v1/ask returns 503.
    app.state.llm = None
    try:
        app.state.llm = create_llm(settings)
    except LLMNotConfigured as exc:
        log.warning("/v1/ask disabled until the LLM is configured: %s", exc)
    app.state.embedder = None
    if settings.preload_embedder:
        try:
            app.state.embedder = E5Embedder()
        except Exception:  # noqa: BLE001
            log.exception("Embedding model could not be loaded; /v1/ask will retry on first call")
    try:
        yield
    finally:
        pool.close()


app = FastAPI(
    title="E-commerce Data API",
    version="1.0.0",
    description=DESCRIPTION,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "health", "description": "Service and database status."},
        {"name": "metrics", "description": "Aggregate indicators: revenue, deliveries."},
        {"name": "products", "description": "Product rankings."},
        {"name": "orders", "description": "Individual orders, with cursor pagination."},
        {"name": "ask", "description": "Natural-language questions, routed to text-to-SQL or to review search (RAG)."},
    ],
)


def _db_unavailable(request: Request, exc: Exception) -> JSONResponse:
    # Details (host, driver message) stay in the log; the client gets a generic message.
    log.warning("Database unavailable on %s %s: %s", request.method, request.url.path, type(exc).__name__)
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        content={"detail": "Database unavailable"})


for exc_type in (DatabaseUnavailable, psycopg.Error, PoolTimeout):
    app.add_exception_handler(exc_type, _db_unavailable)

protected = [Depends(require_api_key)]
app.include_router(health.router, prefix="/v1")
app.include_router(metrics.router, prefix="/v1", dependencies=protected)
app.include_router(products.router, prefix="/v1", dependencies=protected)
app.include_router(orders.router, prefix="/v1", dependencies=protected)
app.include_router(ask.router, prefix="/v1", dependencies=protected)
