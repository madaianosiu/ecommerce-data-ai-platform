"""Data API peste schema core.

Rulare locală (din rădăcina proiectului, cu variabilele din .env):
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
from app.routers import health, metrics, orders, products

log = logging.getLogger("app")

DESCRIPTION = """
API read-only peste modelul dimensional din schema `core` (construit cu dbt din datele Olist).

* **Autentificare:** header `X-API-Key` pe toate endpoint-urile, în afară de `/v1/health`.
* **Venit:** suma `price` pentru comenzile care nu sunt `canceled` / `unavailable`; sumele în EUR
  folosesc cursul BRL→EUR din ziua cumpărării.
* **Erori:** 401 cheie lipsă/greșită, 400/422 parametri invalizi, 503 baza de date indisponibilă.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = create_pool(get_settings())
    pool.open(wait=False)  # nu blocăm pornirea; /v1/health raportează dacă baza nu răspunde
    app.state.pool = pool
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
        {"name": "health", "description": "Starea serviciului și a bazei de date."},
        {"name": "metrics", "description": "Indicatori agregați: venit, livrări."},
        {"name": "products", "description": "Clasamente de produse."},
        {"name": "orders", "description": "Comenzi individuale, cu paginare cu cursor."},
    ],
)


def _db_unavailable(request: Request, exc: Exception) -> JSONResponse:
    # Detaliile (host, mesajul driverului) rămân în log; clientul primește un mesaj generic.
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
