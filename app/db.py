"""Pool de conexiuni psycopg (rol api_reader) și dependența FastAPI care împrumută o conexiune."""
import logging
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from app.config import Settings

log = logging.getLogger("app.db")


class DatabaseUnavailable(Exception):
    """Orice problemă de bază de date; se transformă în 503 fără detalii interne."""


def create_pool(settings: Settings) -> ConnectionPool:
    return ConnectionPool(
        settings.conninfo(),
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        timeout=settings.pool_timeout,
        open=False,  # se deschide în lifespan, fără să blocheze pornirea dacă baza e oprită
        name="api",
        # autocommit: interogări read-only scurte, fără tranzacții rămase deschise.
        kwargs={"row_factory": dict_row, "autocommit": True},
    )


@contextmanager
def connection(request: Request) -> Iterator[psycopg.Connection]:
    pool: ConnectionPool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise DatabaseUnavailable("connection pool is not initialised")
    try:
        with pool.connection() as conn:
            yield conn
    except (psycopg.Error, PoolTimeout) as exc:
        log.exception("Database error")  # detaliile rămân în log, nu ajung la client
        raise DatabaseUnavailable(str(exc)) from exc


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    """Dependență FastAPI: o conexiune din pool pe durata request-ului."""
    with connection(request) as conn:
        yield conn
