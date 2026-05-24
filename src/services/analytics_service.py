"""
Click analytics — recording and aggregation.

REQ: FR-008  — Record click events (timestamp, hashed IP, country, referrer).
REQ: NFR-005 — ip_hash stored, NOT raw IP. SHA-256 + per-deployment salt.
REQ: ADR-03  — Record is a fire-and-forget background task; never blocks redirect.
REQ: AC-009  — Stats endpoint returns totalClicks, clicksByDay, topCountries.
"""

import logging
import uuid
from collections import Counter
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import URL, Click
from src.schemas import ClickByDay, StatsResponse, TopEntry
from src.utils.crypto import hash_ip

logger = logging.getLogger(__name__)


async def record_click(
    db: AsyncSession,
    url_id: uuid.UUID,
    raw_ip: str,
    user_agent: str | None,
    referer: str | None,
) -> None:
    """
    Persist a single click event.

    REQ: FR-008  — record every redirect event.
    REQ: NFR-005 — hash IP before writing; country_code left as None in v1
                   (full GeoIP lookup is a Phase 4+ enhancement).
    REQ: ADR-03  — called via FastAPI BackgroundTasks; never awaited by router.

    Args:
        db:         Async DB session (injected).
        url_id:     PK of the URL that was accessed.
        raw_ip:     Raw client IP (hashed before persistence).
        user_agent: HTTP User-Agent header value.
        referer:    HTTP Referer header value.
    """
    # REQ: NFR-005 — hash, never store raw IP
    ip_hash = hash_ip(raw_ip)

    click = Click(
        url_id=url_id,
        ip_hash=ip_hash,
        user_agent=user_agent[:512] if user_agent else None,
        referer=referer[:512] if referer else None,
        country_code=None,   # Phase 4: add GeoIP lookup here
    )
    db.add(click)
    try:
        await db.commit()
    except Exception as exc:
        logger.error("Failed to record click for url_id=%s: %s", url_id, exc)
        await db.rollback()


async def get_stats(
    db: AsyncSession,
    short_code: str,
    from_date: date | None = None,
    to_date: date | None = None,
) -> StatsResponse | None:
    """
    Aggregate click statistics for a short URL.

    REQ: FR-008  — expose totalClicks, clicksByDay, topCountries, topReferrers.
    REQ: NFR-005 — unique_visitors counted by distinct ip_hash (no raw IPs).
    REQ: AC-009  — no raw IPs in the response payload.

    Args:
        db:         Async DB session.
        short_code: The short code to aggregate.
        from_date:  Start of stats window (inclusive).
        to_date:    End of stats window (inclusive).

    Returns:
        StatsResponse, or None if the URL does not exist.
    """
    # Resolve URL
    url_obj = await db.scalar(select(URL).where(URL.short_code == short_code))
    if url_obj is None:
        return None

    # ── Build click query with optional date filter ───────────────────────
    q = select(Click).where(Click.url_id == url_obj.id)
    if from_date:
        q = q.where(Click.clicked_at >= datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc))
    if to_date:
        from datetime import timedelta
        end = datetime(to_date.year, to_date.month, to_date.day, tzinfo=timezone.utc) + timedelta(days=1)
        q = q.where(Click.clicked_at < end)

    result = await db.execute(q)
    clicks = result.scalars().all()

    total = len(clicks)
    # REQ: NFR-005 — uniqueness by hashed IP, not raw IP
    unique = len({c.ip_hash for c in clicks})

    # ── clicks_by_day aggregation ─────────────────────────────────────────
    day_counter: Counter[str] = Counter()
    for c in clicks:
        day_str = c.clicked_at.strftime("%Y-%m-%d")
        day_counter[day_str] += 1

    clicks_by_day = [
        ClickByDay(date=day, clicks=count)
        for day, count in sorted(day_counter.items())
    ]

    # ── top countries ─────────────────────────────────────────────────────
    country_counter: Counter[str] = Counter(
        c.country_code for c in clicks if c.country_code
    )
    top_countries = [
        TopEntry(value=cc, clicks=cnt)
        for cc, cnt in country_counter.most_common(10)
    ]

    # ── top referrers ─────────────────────────────────────────────────────
    referer_counter: Counter[str] = Counter(
        c.referer for c in clicks if c.referer
    )
    top_referrers = [
        TopEntry(value=ref, clicks=cnt)
        for ref, cnt in referer_counter.most_common(10)
    ]

    return StatsResponse(
        shortCode=short_code,
        totalClicks=total,
        uniqueVisitors=unique,
        clicksByDay=clicks_by_day,
        topCountries=top_countries,
        topReferrers=top_referrers,
    )
