"""Postgres connection pool — psycopg3."""

import logging
import os
import socket
from typing import Optional
from contextlib import contextmanager
from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

_pool: Optional[ConnectionPool] = None


def _resolve_pooler_host(region: str) -> str:
    """Try aws-0 and aws-1 prefixes to find the working pooler hostname."""
    for prefix in ("aws-0", "aws-1"):
        host = f"{prefix}-{region}.pooler.supabase.com"
        try:
            socket.getaddrinfo(host, 5432, socket.AF_INET, socket.SOCK_STREAM)
            return host
        except socket.gaierror:
            continue
    # fallback
    return f"aws-0-{region}.pooler.supabase.com"


def _normalise_supabase_url(raw: str) -> str:
    """
    Ensure the Supabase connection string uses the **session-mode pooler**
    (IPv4-compatible, supports prepared statements).

    Expected format:
      postgresql://postgres.<PROJECT_REF>:<PASSWORD>@aws-N-<REGION>.pooler.supabase.com:5432/postgres
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
    # Pooler: aws-N-<region>.pooler.supabase.com  (ref is in username)
    elif "pooler.supabase.com" in p.hostname:
        parts = p.hostname.split(".")
        if parts[0].startswith("aws-"):
            # e.g. aws-1-ap-northeast-2 -> region = ap-northeast-2
            region = "-".join(parts[0].split("-")[2:])
        if p.username and "." in p.username:
            project_ref = p.username.split(".", 1)[1]
        # If already using pooler with session-mode port 5432, return as-is
        if str(p.port) == "5432" and project_ref:
            return raw

    if not project_ref:
        return raw  # can't normalise - return as-is

    # Build session-mode pooler URL
    pooler_host = _resolve_pooler_host(region)
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
