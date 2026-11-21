"""psycopg connection pool (role api_reader) and the FastAPI dependency that lends a connection."""
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
    """Any database problem; turned into a 503 without internal details."""


def create_pool(settings: Settings) -> ConnectionPool:
    return ConnectionPool(
        settings.conninfo(),
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        timeout=settings.pool_timeout,
        open=False,  # opened in lifespan, without blocking startup if the database is down
        name="api",
        # autocommit: short read-only queries, no transactions left open.
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
        log.exception("Database error")  # details stay in the log, never reach the client
        raise DatabaseUnavailable(str(exc)) from exc


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    """FastAPI dependency: a pool connection for the duration of the request."""
    with connection(request) as conn:
        yield conn
