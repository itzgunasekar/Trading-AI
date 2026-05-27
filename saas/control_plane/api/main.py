"""
D1 SaaS Control Plane — FastAPI entrypoint.

Layout:
    api/main.py           ← this file (FastAPI app + route mounting)
    api/routes/           ← route modules (auth, user, admin, billing)
    auth/                 ← password hashing, JWT, MFA
    admin/                ← admin operations
    billing/              ← Stripe integration, fee calculation
    security/             ← encryption helpers (already done)

The control plane NEVER imports strategy code.
Strategy code lives only in the per-user bot containers.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

# Stub imports — these modules to be filled in next
from api.routes import health, auth, admin, user, billing

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    log.info("=== D1 SaaS Control Plane starting ===")
    # Verify required env vars are set
    required = ["D1BOT_KEK_HEX", "DATABASE_URL", "JWT_SECRET"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        log.warning(f"Missing env vars (some features will fail): {missing}")
    # TODO: connect to DB pool, run migrations, etc.
    yield
    log.info("=== D1 SaaS Control Plane stopping ===")


app = FastAPI(
    title="D1 Portfolio Bot SaaS — Control Plane",
    description="Multi-tenant API for the D1 Portfolio trading bot. NO strategy code.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if os.environ.get("ENV") != "production" else None,    # disable Swagger in prod
    redoc_url="/redoc" if os.environ.get("ENV") != "production" else None,
)

# --- Security middleware -----------------------------------------------------
# In production, replace allowed_hosts with your real domain
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(","),
)

# CORS — frontend origin only. Tighten in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# --- Security headers --------------------------------------------------------
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    return response


# --- Global exception handler ------------------------------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    log.exception(f"Unhandled exception on {request.url}")
    # Never leak internals to the client
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "request_id": str(id(request))},
    )


# --- Routes ------------------------------------------------------------------
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(user.router)
app.include_router(admin.router)
app.include_router(billing.router)
