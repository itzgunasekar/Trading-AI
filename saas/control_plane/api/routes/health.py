"""Health-check endpoint — always returns 200 if app is alive."""

from fastapi import APIRouter
from datetime import datetime, timezone

router = APIRouter()


@router.get("/health", tags=["health"])
async def health():
    return {
        "status": "ok",
        "service": "d1-saas-control-plane",
        "version": "0.1.0",
        "ts": datetime.now(timezone.utc).isoformat(),
    }
