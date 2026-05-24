"""
API key authentication dependency.

REQ: NFR-004 — All write endpoints MUST require a valid API key in X-API-Key header.
               Redirect (GET /{shortCode}) and GET /health are exempt.
REQ: AC-010  — Endpoints return 401 when X-API-Key is absent or invalid.

The API key is compared against the stored SHA-256 hash (crypto.hash_api_key).
In a real deployment, this lookup would hit a short-lived in-memory cache
to avoid a DB query on every request.
"""

import logging

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_db
from src.models import User
from src.utils.crypto import hash_api_key

logger = logging.getLogger(__name__)

# ── Header extractor (REQ: NFR-004) ──────────────────────────────────────────
_api_key_header = APIKeyHeader(
    name=settings.api_key_header,
    auto_error=False,  # We return a custom 401 body
)


async def require_api_key(
    raw_key: str | None = Security(_api_key_header),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI dependency: validate X-API-Key and return the matching User.

    REQ: NFR-004 — returns 401 on missing/invalid key.
    REQ: AC-010  — 100% of write endpoints return 401 on missing/invalid key.

    Usage:
        @router.post("/urls")
        async def create(current_user: User = Depends(require_api_key)):
            ...
    """
    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "X-API-Key header is required."},
        )

    key_hash = hash_api_key(raw_key)
    user = await db.scalar(
        select(User).where(User.api_key_hash == key_hash, User.is_active == True)  # noqa: E712
    )

    if user is None:
        logger.warning("Invalid API key attempted (hash prefix=%s…)", key_hash[:8])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or inactive API key."},
        )

    return user
