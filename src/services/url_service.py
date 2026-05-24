"""
Core URL business logic.

REQ: FR-001  — create_url: validate, generate/accept alias, persist, warm cache.
REQ: FR-002  — resolve_url: cache-first lookup, return longUrl for redirect.
REQ: FR-005  — delete_url: owner check (FIND-002 fix), soft-delete, evict cache.
REQ: FR-006  — resolve_url: return None with 'expired' status on expired URL.
REQ: FR-007  — create_url: accept custom alias, 409 on conflict.
REQ: FR-009  — create_url: accept expiresAt / ttl, persist expires_at.
REQ: NFR-006 — resolve_url: Redis cache-aside; DB read only on miss.
REQ: ADR-04  — short code: NanoID 8-char with retry on collision.

Self-critique fixes applied (from reviews/initial-code-review.json):
  FIND-002: Missing ownership check in delete_url → added user_id assertion.
  FIND-003: TTL duration (ISO 8601) not parsed → added _parse_ttl helper.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.cache import cache_delete, cache_get, cache_set
from src.config import settings
from src.models import URL
from src.schemas import CreateUrlRequest, UrlResponse
from src.services.validator import validate_url
from src.utils.codegen import generate_short_code

logger = logging.getLogger(__name__)


# ── Return sentinel for resolve_url ──────────────────────────────────────────
class ResolveStatus(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    DELETED = "deleted"


class ResolveResult:
    """Typed result for resolve_url avoiding bare tuples."""

    def __init__(self, status: ResolveStatus, long_url: str | None = None) -> None:
        self.status = status
        self.long_url = long_url


# ── TTL helper  (self-critique fix: FIND-003) ─────────────────────────────────
def _parse_ttl(ttl_str: str) -> timedelta:
    """
    Parse an ISO 8601 duration string into a timedelta.

    Supports: P{n}D (days), P{n}W (weeks), PT{n}H (hours), PT{n}M (minutes).
    Self-critique fix FIND-003: initial version ignored the ttl field entirely.

    REQ: FR-009 — support ISO 8601 duration as alternative to expiresAt.
    """
    import re

    patterns = [
        (r"^P(\d+)W$", lambda m: timedelta(weeks=int(m.group(1)))),
        (r"^P(\d+)D$", lambda m: timedelta(days=int(m.group(1)))),
        (r"^PT(\d+)H$", lambda m: timedelta(hours=int(m.group(1)))),
        (r"^PT(\d+)M$", lambda m: timedelta(minutes=int(m.group(1)))),
    ]
    for pattern, builder in patterns:
        m = re.match(pattern, ttl_str.upper())
        if m:
            return builder(m)
    raise ValueError(f"Unsupported ISO 8601 duration: {ttl_str!r}")


# ── Service functions ─────────────────────────────────────────────────────────

async def create_url(
    db: AsyncSession,
    request: CreateUrlRequest,
    user_id: uuid.UUID,
) -> UrlResponse:
    """
    Validate, persist, and cache a new short URL.

    REQ: FR-001  — accept longUrl, return shortCode.
    REQ: FR-003  — validate URL before persistence.
    REQ: FR-007  — handle custom alias; 409 on conflict.
    REQ: FR-009  — resolve expiresAt from expiresAt or ttl field.
    REQ: NFR-006 — write-through cache warm-up on creation.
    REQ: ADR-04  — retry loop on NanoID collision.
    """
    # ── Validate URL (REQ: FR-003, NFR-003) ──────────────────────────────
    validate_url(request.long_url)  # raises URLValidationError on failure

    # ── Resolve expiry (REQ: FR-009, FIND-003 fix) ───────────────────────
    expires_at: datetime | None = request.expires_at
    if request.ttl and expires_at is None:
        delta = _parse_ttl(request.ttl)
        expires_at = datetime.now(timezone.utc) + delta

    # ── Alias / short code (REQ: FR-007, ADR-04) ─────────────────────────
    if request.alias:
        # Check alias availability
        existing = await db.scalar(
            select(URL).where(URL.short_code == request.alias, URL.deleted_at.is_(None))
        )
        if existing is not None:
            raise ValueError("ALIAS_TAKEN")
        short_code = request.alias
    else:
        # Collision-resistant retry loop (REQ: ADR-04)
        short_code = await _generate_unique_code(db)

    # ── Persist (REQ: FR-001) ─────────────────────────────────────────────
    url_obj = URL(
        user_id=user_id,
        long_url=request.long_url,
        short_code=short_code,
        custom_alias=request.alias,
        expires_at=expires_at,
    )
    db.add(url_obj)
    await db.flush()   # assign id without committing (commit in get_db)

    # ── Cache warm-up (REQ: NFR-006) ──────────────────────────────────────
    ttl_seconds = (
        int((expires_at - datetime.now(timezone.utc)).total_seconds())
        if expires_at
        else settings.cache_ttl_seconds
    )
    await cache_set(short_code, url_obj.long_url, ttl=max(ttl_seconds, 1))

    logger.info("URL created: short_code=%s user_id=%s", short_code, user_id)

    return _to_response(url_obj)


async def resolve_url(db: AsyncSession, short_code: str) -> ResolveResult:
    """
    Resolve a short code to its long URL (cache-first).

    REQ: FR-002  — return longUrl for 302 redirect.
    REQ: FR-006  — return EXPIRED status if expires_at has passed.
    REQ: NFR-006 — Redis cache-aside; DB read only on cache miss.
    REQ: ADR-03  — Redis failure falls through to DB, never raises 5xx.
    """
    # ── Cache hit path (REQ: NFR-006) ──────────────────────────────────────
    cached = await cache_get(short_code)
    if cached is not None:
        return ResolveResult(status=ResolveStatus.OK, long_url=cached)

    # ── DB lookup on cache miss ────────────────────────────────────────────
    url_obj = await db.scalar(
        select(URL).where(URL.short_code == short_code)
    )

    if url_obj is None:
        return ResolveResult(status=ResolveStatus.NOT_FOUND)

    if url_obj.is_deleted:
        return ResolveResult(status=ResolveStatus.DELETED)

    if url_obj.is_expired:
        return ResolveResult(status=ResolveStatus.EXPIRED)

    # ── Re-warm cache (REQ: NFR-006) ───────────────────────────────────────
    await cache_set(short_code, url_obj.long_url)

    return ResolveResult(status=ResolveStatus.OK, long_url=url_obj.long_url)


async def get_url(db: AsyncSession, short_code: str) -> URL | None:
    """
    Fetch URL metadata for the management endpoint (no redirect).
    REQ: FR-001 (GET metadata variant).
    """
    return await db.scalar(
        select(URL).where(URL.short_code == short_code, URL.deleted_at.is_(None))
    )


async def delete_url(
    db: AsyncSession,
    short_code: str,
    requesting_user_id: uuid.UUID,
) -> bool:
    """
    Soft-delete a URL, verifying ownership first.

    REQ: FR-005  — owner delete → subsequent GET returns 404.
    REQ: ADR-02  — soft delete (deleted_at) not hard delete.
    Self-critique fix FIND-002 — ownership check added; initial version
    deleted any URL without verifying requesting_user_id == url_obj.user_id.
    """
    url_obj = await db.scalar(
        select(URL).where(URL.short_code == short_code, URL.deleted_at.is_(None))
    )

    if url_obj is None:
        return False  # caller should return 404

    # ── FIND-002 fix: ownership guard (REQ: FR-005, A01 OWASP) ───────────
    if url_obj.user_id != requesting_user_id:
        raise PermissionError("FORBIDDEN")

    # ── Soft delete (REQ: ADR-02) ─────────────────────────────────────────
    url_obj.deleted_at = datetime.now(timezone.utc)
    url_obj.is_active = False
    await db.flush()

    # ── Evict cache immediately (REQ: FR-005) ─────────────────────────────
    await cache_delete(short_code)

    logger.info("URL soft-deleted: short_code=%s by user_id=%s", short_code, requesting_user_id)
    return True


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _generate_unique_code(db: AsyncSession) -> str:
    """
    Generate a short code that doesn't already exist in the DB.

    REQ: ADR-04 — max retries = settings.short_code_max_retries (default 3).
    """
    for attempt in range(settings.short_code_max_retries):
        code = generate_short_code()
        existing = await db.scalar(select(URL).where(URL.short_code == code))
        if existing is None:
            return code
        logger.warning("Short code collision on attempt %d: %s", attempt + 1, code)

    raise RuntimeError(
        "Failed to generate a unique short code after "
        f"{settings.short_code_max_retries} attempts."
    )


def _to_response(url_obj: URL) -> UrlResponse:
    """Map an ORM URL object to the UrlResponse schema."""
    return UrlResponse(
        shortCode=url_obj.short_code,
        shortUrl=f"{settings.base_url}/{url_obj.short_code}",
        longUrl=url_obj.long_url,
        alias=url_obj.custom_alias,
        createdAt=url_obj.created_at,
        expiresAt=url_obj.expires_at,
        isActive=url_obj.is_active,
    )
