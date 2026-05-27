"""Postgres connection pool — psycopg3."""

import logging
import os
from typing import Optional
from contextlib import contextmanager
from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

_pool: Optional[ConnectionPool] = None


def _normalise_supabase_url(raw: str) -> str:
    """
    Ensure the Supabase connection string uses the **session-mode pooler**
    (IPv4-compatible, supports prepared statements).

    Expected format:
      postgresql://postgres.<PROJECT_REF>:<PASSWORD>@aws-0-<REGION>.pooler.supabase.com:5432/postgres

    Common mistakes this fixes:
      - Direct host (db.<ref>.supabase.co:5432) on IPv4-only infra -> pooler
      - Transaction-mode port 6543 -> session-mode 5432
    """
    p = urlparse(raw)

    # Only touch Supabase URLs
    if not p.hostname or "supabase" not in p.hostname:
        return raw

    project_ref = None
    region = "ap-northeast-2"  # default; overridden below if detectable

    # --- detect project ref ---
    # Direct: db.<ref>.supabase.co
    if p.hostname.startswith("db.") and p.hostname.endswith(".supabase.co"):
        project_ref = p.hostname.split(".")[1]
    # Pooler: aws-0-<region>.pooler.supabase.com  (ref is in username)
    elif "pooler.supabase.com" in p.hostname:
        parts = p.hostname.split(".")
        # e.g. aws-0-ap-northeast-2
        if parts[0].startswith("aws-"):
            region = parts[0].replace("aws-0-", "")
        if p.username and "." in p.username:
            project_ref = p.username.split(".", 1)[1]

    if not project_ref:
        return raw  # can't normalise - return as-is

    # Build session-mode pooler URL
    pooler_host = f"aws-0-{region}.pooler.supabase.com"
    username = f"postgres.{project_ref}"
    password = p.password or ""
    new = urlunparse((
        "postgresql",
        f"{username}:{password}@{pooler_host}:5432",
        p.path or "/postgres",
        p.params,
        p.query,
        p.fragment,
    ))
    if new != raw:
        log.info("DATABASE_URL normalised to session-mode pooler (%s:5432)", pooler_host)
    return new


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL not set")
        url = _normalise_supabase_url(url)
        log.info("Connecting to DB at host=%s", urlparse(url).hostname)
        _pool = ConnectionPool(url, min_size=2, max_size=10, kwargs={"row_factory": psycopg.rows.dict_row})
    return _pool


@contextmanager
def conn():
    """Yield a connection from the pool."""
    with get_pool().connection() as c:
        yield c
