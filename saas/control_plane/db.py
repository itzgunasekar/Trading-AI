"""Postgres connection pool — psycopg3."""

import os
from typing import Optional
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool


_pool: Optional[ConnectionPool] = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL not set")
        _pool = ConnectionPool(url, min_size=2, max_size=10, kwargs={"row_factory": psycopg.rows.dict_row})
    return _pool


@contextmanager
def conn():
    """Yield a connection from the pool."""
    with get_pool().connection() as c:
        yield c
