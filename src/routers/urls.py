"""
URL management endpoints.

REQ: FR-001  — POST /api/v1/urls  → 201 UrlResponse
REQ: FR-005  — DELETE /api/v1/urls/{shortCode} → 204
REQ: FR-007  — Custom alias support in POST body
REQ: NFR-004 — All endpoints require API key (via rate_limit dependency)
REQ: AC-001  — shortUrl matches pattern https://sho.rt/[A-Za-z0-9]{6,10}
REQ: AC-007  — Custom alias → 409 on conflict
REQ: SC-001, SC-003, SC-007, SC-009
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.middleware.rate_limiter import rate_limit
from src.models import User
from src.schemas import CreateUrlRequest, ErrorResponse, UrlResponse
from src.services import url_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/urls", tags=["URLs"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=UrlResponse,
    responses={
        401: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
    summary="Create a short URL",  # noqa: W605
)
async def create_short_url(
    body: CreateUrlRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(rate_limit),   # auth + rate-limit in one dep
) -> UrlResponse:
    """
    Accept a long URL and return a short code.

    REQ: FR-001  — validate + persist + return shortUrl.
    REQ: FR-003  — URL validation happens inside url_service.create_url.
    REQ: FR-007  — optional alias; 409 if already taken.
    REQ: FR-009  — optional expiresAt or ttl.
    REQ: NFR-004 — auth enforced via rate_limit dependency chain.
    """
    try:
        response = await url_service.create_url(db, body, current_user.id)
        return response

    except ValueError as exc:
        # URLValidationError is a ValueError subclass — extract .code when present
        err_code: str = getattr(exc, "code", None) or str(exc)
        err_msg:  str = getattr(exc, "detail", str(exc))

        if err_code == "ALIAS_TAKEN":
            # REQ: FR-007, SC-007 — 409 on alias conflict
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "ALIAS_TAKEN", "message": "This alias is already in use."},
            )
        # INVALID_SCHEME, SSRF_BLOCKED, INVALID_URL → 422
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": err_code, "message": err_msg},
        )
    except Exception:
        logger.exception("Unexpected error in create_short_url")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."},
        )


@router.get(
    "/{short_code}",
    response_model=UrlResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    summary="Get URL metadata (does not redirect)",
)
async def get_url_metadata(
    short_code: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(rate_limit),
) -> UrlResponse:
    """
    Return metadata for a short URL without redirecting.
    REQ: FR-001 (GET variant), NFR-004.
    """
    url_obj = await url_service.get_url(db, short_code)
    if url_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SHORT_CODE_NOT_FOUND", "message": f"Short code '{short_code}' not found."},
        )
    return url_service._to_response(url_obj)


@router.delete(
    "/{short_code}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    summary="Delete a short URL (owner only)",
)
async def delete_url(
    short_code: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(rate_limit),
) -> None:
    """
    Soft-delete a short URL. After deletion, GET /{shortCode} returns 404.

    REQ: FR-005  — owner may delete their own URLs.
    REQ: ADR-02  — soft delete (deleted_at set), not hard delete.
    Self-critique fix FIND-002 — ownership verified inside url_service.delete_url.
    REQ: SC-009  — Gherkin: DELETE → 204; subsequent GET → 404.
    """
    try:
        found = await url_service.delete_url(db, short_code, current_user.id)
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "You do not own this short URL."},
        )

    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SHORT_CODE_NOT_FOUND", "message": f"Short code '{short_code}' not found."},
        )
