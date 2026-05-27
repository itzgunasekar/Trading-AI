"""Health-check endpoints -- always return 200 if app is alive."""

from fastapi import APIRouter
from datetime import datetime, timezone

router = APIRouter()


@router.get("/", tags=["health"])
async def root():
    """Root endpoint -- Render health checks and browsers hit this."""
    return {
        "status": "ok",
        "service": "d1-saas-control-plane",
        "version": "0.1.0",
        "docs": "/docs",
    }


@router.get("/health", tags=["health"])
async def health():
    return {
        "status": "ok",
        "service": "d1-saas-control-plane",
        "version": "0.1.0",
        "ts": datetime.now(timezone.utc).isoformat(),
    }
