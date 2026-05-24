"""
Health check endpoint.

REQ: NFR-007 — availability check for load-balancer probes.
No authentication required (REQ: NFR-004 exception).
"""

from fastapi import APIRouter
from sqlalchemy import text
from src.cache import ping as redis_ping
from src.database import AsyncSessionLocal
from src.schemas import HealthCheckDetail, HealthResponse
from src.config import settings

router = APIRouter(tags=["Operations"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    include_in_schema=True,
)
async def health_check() -> HealthResponse:
    """
    Checks DB and Redis connectivity.
    REQ: NFR-007 — returns 'ok' or 'degraded'.
    """
    db_ok = False
    async with AsyncSessionLocal() as session:
        try:
            await session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False

    cache_ok = await redis_ping()

    overall = "ok" if (db_ok and cache_ok) else "degraded"

    return HealthResponse(
        status=overall,
        version=settings.app_version,
        checks=HealthCheckDetail(
            database="ok" if db_ok else "error",
            cache="ok" if cache_ok else "error",
        ),
    )
