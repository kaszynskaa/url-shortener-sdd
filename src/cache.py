"""
Redis async cache wrapper — cache-aside for the redirect hot path.

REQ: NFR-006 — All redirect hot-path reads hit cache first; DB only on miss.
REQ: FR-004  — Rate-limit counters stored here (INCR + EXPIRE).
REQ: ADR-03  — Redis unavailability must not block redirects (fallback to DB).
"""

import logging

import redis.asyncio as aioredis

from src.config import settings

logger = logging.getLogger(__name__)

# Module-level pool shared across requests
_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return (or lazily create) the global Redis client."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    return _redis


async def close_redis() -> None:
    """Close Redis connection pool on app shutdown."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


# ── Cache operations ──────────────────────────────────────────────────────────

async def cache_get(key: str) -> str | None:
    """
    Read a value from Redis.
    REQ: NFR-006 — returns None (not raises) on cache miss or Redis error.
    """
    try:
        r = await get_redis()
        return await r.get(key)
    except Exception as exc:
        # REQ: ADR-03 — Redis down must not block redirects
        logger.warning("cache_get failed for key=%s: %s", key, exc)
        return None


async def cache_set(key: str, value: str, ttl: int | None = None) -> None:
    """
    Write a value to Redis with optional TTL.
    REQ: NFR-006 — write-through on URL creation warms the cache.
    """
    try:
        r = await get_redis()
        if ttl:
            await r.set(key, value, ex=ttl)
        else:
            await r.set(key, value, ex=settings.cache_ttl_seconds)
    except Exception as exc:
        logger.warning("cache_set failed for key=%s: %s", key, exc)


async def cache_delete(key: str) -> None:
    """
    Evict a key on URL deletion.
    REQ: FR-005 — after soft-delete, redirect must return 404 immediately.
    """
    try:
        r = await get_redis()
        await r.delete(key)
    except Exception as exc:
        logger.warning("cache_delete failed for key=%s: %s", key, exc)


async def cache_incr(key: str, window_seconds: int) -> int:
    """
    Increment a counter and set expiry on first increment.
    REQ: FR-004 — sliding-window rate-limit counter.
    Returns the new counter value.
    """
    r = await get_redis()
    pipe = r.pipeline()
    await pipe.incr(key)
    await pipe.expire(key, window_seconds)
    results = await pipe.execute()
    return int(results[0])


async def ping() -> bool:
    """Health-check: returns True if Redis is reachable."""
    try:
        r = await get_redis()
        return await r.ping()
    except Exception:
        return False
