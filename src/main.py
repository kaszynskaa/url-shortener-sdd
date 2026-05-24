"""
FastAPI application factory + lifespan.

REQ: COMP-01  — Hosts all HTTP endpoints, manages startup/shutdown lifecycle.
REQ: NFR-007  — Startup validates DB and Redis connectivity.
REQ: ADR-01   — Redirect router uses 302.
REQ: ARCH-20260524-001 Phase 3 — wires all routers and middleware.
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.cache import close_redis, get_redis
from src.config import settings
from src.database import create_tables
from src.routers import health, redirect, stats, urls

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan: startup → yield → shutdown.
    REQ: COMP-01 — establishes DB pool and Redis connection on startup.
    """
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)

    # ── Startup ────────────────────────────────────────────────────────────
    await create_tables()         # no-op if tables exist (idempotent)
    await get_redis()             # warm connection pool
    logger.info("Database and cache connections established.")

    yield

    # ── Shutdown ───────────────────────────────────────────────────────────
    await close_redis()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """Application factory — returns a configured FastAPI instance."""
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "URL Shortener Service — SPEC-20260524-001. "
            "See specs/url-shortener.yaml for full OpenAPI contract."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── Global error handler ───────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url)
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."},
        )

    # ── Routers ────────────────────────────────────────────────────────────
    # Order matters: more specific paths first to avoid shadowing.
    app.include_router(health.router)          # GET /health
    app.include_router(urls.router)            # POST|GET|DELETE /api/v1/urls/...
    app.include_router(stats.router)           # GET /api/v1/urls/{code}/stats
    app.include_router(redirect.router)        # GET /{shortCode}  ← catch-all last

    return app


# ── Entry point ───────────────────────────────────────────────────────────────
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=settings.debug)
