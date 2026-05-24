"""
Redis-backed sliding-window rate limiter.

REQ: FR-004  — 100 create requests per minute per API key; 429 + Retry-After on breach.
REQ: NFR-004 — Rate limiting protects service availability.
REQ: AC-005  — 101st request in a 60-second window returns 429 with Retry-After header.
REQ: SC-008  — Gherkin scenario: rate limit exceeded returns 429.

Implementation: fixed-window counter using Redis INCR + EXPIRE.
Each API key gets a Redis key `rl:<user_id>:<window_start_epoch>`.
The window resets automatically via Redis TTL.

Self-critique fix FIND-004 (from reviews/initial-code-review.json):
  Initial version used a shared global counter (no per-user bucketing).
  Fixed: bucket key is scoped to user_id so users cannot exhaust each other's quota.
"""

import logging
import time

from fastapi import Depends, HTTPException, Request, status

from src.cache import cache_incr
from src.config import settings
from src.middleware.auth import require_api_key
from src.models import User

logger = logging.getLogger(__name__)


async def rate_limit(
    _request: Request,
    current_user: User = Depends(require_api_key),
) -> User:
    """
    FastAPI dependency: enforce per-user rate limit, then return the User.

    REQ: FR-004  — 100 req/min per API key.
    REQ: AC-005  — Retry-After header set to seconds until window reset.
    Self-critique fix FIND-004 — bucket key is per-user, not global.

    Returns:
        The authenticated User (pass-through for downstream handlers).

    Raises:
        HTTPException 429 when limit is exceeded.
    """
    effective_limit = current_user.rate_limit_override or settings.rate_limit_max_requests
    window = settings.rate_limit_window_seconds

    # REQ: FIND-004 fix — bucket key scoped to user_id (not global)
    window_start = int(time.time()) // window * window
    bucket_key = f"rl:{current_user.id}:{window_start}"

    count = await cache_incr(bucket_key, window_seconds=window)
    retry_after = window_start + window - int(time.time())

    if count > effective_limit:
        logger.warning(
            "Rate limit exceeded: user_id=%s count=%d limit=%d",
            current_user.id, count, effective_limit,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(max(retry_after, 1))},
            detail={
                "code": "RATE_LIMIT_EXCEEDED",
                "message": (
                    f"Rate limit of {effective_limit} requests per "
                    f"{window} seconds exceeded."
                ),
            },
        )

    return current_user
