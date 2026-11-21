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
from app.embedder import E5Embedder
from app.llm import LLMNotConfigured, create_llm
from app.routers import ask, health, metrics, orders, products

log = logging.getLogger("app")

DESCRIPTION = """
API read-only peste modelul dimensional din schema `core` (construit cu dbt din datele Olist).

* **Autentificare:** header `X-API-Key` pe toate endpoint-urile, în afară de `/v1/health`.
* **Venit:** suma `price` pentru comenzile care nu sunt `canceled` / `unavailable`; sumele în EUR
  folosesc cursul BRL→EUR din ziua cumpărării.
* **`/v1/ask`:** răspuns generat de un LLM doar din recenziile scrise găsite prin căutare semantică,
  cu citări [n]. Recenziile scrise sunt un subset înclinat spre scoruri mici.
* **Erori:** 401 cheie lipsă/greșită, 400/422 parametri invalizi, 503 baza de date sau LLM-ul
  indisponibil temporar, 502 eroare a LLM-ului.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    pool = create_pool(settings)
    pool.open(wait=False)  # nu blocăm pornirea; /v1/health raportează dacă baza nu răspunde
    app.state.pool = pool

    # /v1/ask: clientul LLM (fără apel de rețea) și modelul de embedding, încărcat o singură dată.
    # Dacă oricare lipsește, celelalte endpoint-uri merg normal; /v1/ask răspunde 503.
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
        {"name": "health", "description": "Starea serviciului și a bazei de date."},
        {"name": "metrics", "description": "Indicatori agregați: venit, livrări."},
        {"name": "products", "description": "Clasamente de produse."},
        {"name": "orders", "description": "Comenzi individuale, cu paginare cu cursor."},
        {"name": "ask", "description": "Întrebări în limbaj natural, cu răspuns din recenzii (RAG)."},
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
app.include_router(ask.router, prefix="/v1", dependencies=protected)
