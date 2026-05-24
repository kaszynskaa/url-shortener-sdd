"""
Analytics / stats endpoint.

REQ: FR-008  — GET /api/v1/urls/{shortCode}/stats returns click metrics.
REQ: NFR-005 — Stats contain no raw IPs (uniqueVisitors by hashed ip_hash).
REQ: AC-009  — Returns totalClicks, clicksByDay, topCountries; no raw IPs.
REQ: NFR-004 — Owner-only; requires auth.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.middleware.auth import require_api_key
from src.models import User
from src.schemas import ErrorResponse, StatsResponse
from src.services.analytics_service import get_stats
from src.services.url_service import get_url

router = APIRouter(prefix="/api/v1/urls", tags=["Analytics"])


@router.get(
    "/{short_code}/stats",
    response_model=StatsResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    summary="Get click analytics for a short URL",
)
async def get_url_stats(
    short_code: str,
    from_date: date | None = None,
    to_date: date | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_api_key),   # auth only; no rate-limit on reads
) -> StatsResponse:
    """
    Return aggregate click statistics for a short URL.

    REQ: FR-008  — expose totalClicks, clicksByDay, topCountries, topReferrers.
    REQ: NFR-005 — unique visitors counted by distinct ip_hash (no raw IPs).
    REQ: AC-009  — verified no raw IPs in stats response.
    """
    # ── Existence + ownership check ───────────────────────────────────────
    url_obj = await get_url(db, short_code)
    if url_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SHORT_CODE_NOT_FOUND", "message": f"'{short_code}' not found."},
        )

    if url_obj.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "You do not own this short URL."},
        )

    stats = await get_stats(db, short_code, from_date=from_date, to_date=to_date)
    if stats is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SHORT_CODE_NOT_FOUND", "message": f"'{short_code}' not found."},
        )
    return stats
