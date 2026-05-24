"""
Public redirect endpoint — the hot path.

REQ: FR-002  — GET /{shortCode} → 302 redirect to longUrl within 50 ms p99.
REQ: FR-006  — 410 Gone for expired URLs (SC-006).
REQ: NFR-001 — p99 ≤ 50 ms. Redis cache-first; DB only on miss.
REQ: NFR-006 — Cache hit rate ≥ 95%.
REQ: FR-008  — Record click event asynchronously (BackgroundTasks).
REQ: AC-002  — Verified by load test; Cache-Control: no-store per ADR-01.
REQ: SC-002, SC-006, SC-010
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.schemas import ErrorResponse
from src.services import analytics_service, url_service
from src.services.url_service import ResolveStatus

logger = logging.getLogger(__name__)

# No prefix — this router handles the root /{shortCode} path
router = APIRouter(tags=["Redirect"])


@router.get(
    "/{short_code}",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    responses={
        302: {"description": "Redirect to original URL"},
        404: {"model": ErrorResponse, "description": "Short code not found"},
        410: {"model": ErrorResponse, "description": "URL has expired"},
    },
    summary="Redirect to the original URL",
    # REQ: NFR-004 — public endpoint; no auth required
    include_in_schema=True,
)
async def redirect_to_long_url(
    short_code: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """
    Cache-first redirect. Resolves short code → longUrl and issues 302.

    REQ: FR-002  — 302 redirect with Location header.
    REQ: FR-006  — 410 Gone if expired.
    REQ: NFR-001 — target p99 ≤ 50 ms; Redis hit avoids DB read.
    REQ: ADR-01  — 302 (not 301) so analytics work and browsers don't cache.
    REQ: FR-008  — click recorded via BackgroundTasks (non-blocking).
    REQ: NFR-005 — raw IP passed to analytics_service which hashes it.
    """
    result = await url_service.resolve_url(db, short_code)

    if result.status == ResolveStatus.NOT_FOUND or result.status == ResolveStatus.DELETED:
        # REQ: SC-010 — 404 for unknown / deleted codes
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SHORT_CODE_NOT_FOUND", "message": f"'{short_code}' not found."},
        )

    if result.status == ResolveStatus.EXPIRED:
        # REQ: FR-006, SC-006 — 410 Gone; no redirect, no click event
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": "URL_EXPIRED", "message": f"'{short_code}' has expired."},
        )

    # ── Record click asynchronously (REQ: FR-008, ADR-03) ─────────────────
    # Resolve client IP (X-Forwarded-For header in prod behind a proxy)
    client_ip = (
        request.headers.get("x-forwarded-for", request.client.host)
        if request.client
        else "unknown"
    )
    # Strip potential comma-separated list to first IP
    client_ip = client_ip.split(",")[0].strip()

    # We need the URL's id for analytics; fetch from DB if came from cache
    url_obj = await url_service.get_url(db, short_code)
    if url_obj is not None:
        background_tasks.add_task(
            analytics_service.record_click,
            db=db,
            url_id=url_obj.id,
            raw_ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            referer=request.headers.get("referer"),
        )

    # REQ: FR-002, ADR-01 — 302 + Cache-Control: no-store
    return RedirectResponse(
        url=result.long_url,
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "no-store"},
    )
