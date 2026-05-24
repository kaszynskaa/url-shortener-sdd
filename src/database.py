"""
Async SQLAlchemy engine + session factory.

REQ: COMP-02 — PostgreSQL async connection pool.
REQ: NFR-007 — Pool pre-ping detects stale connections before query.

SQLite note: pool_size / max_overflow / pool_pre_ping are PostgreSQL tuning
parameters. When DATABASE_URL is sqlite+aiosqlite (test/dev), we use
StaticPool so the single in-memory DB is shared across async contexts.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import settings
from src.models import Base


def _build_engine() -> AsyncEngine:
    """Build an async engine appropriate for the configured DATABASE_URL."""
    url = settings.database_url

    if url.startswith("sqlite"):
        # ── SQLite (in-memory for tests) ───────────────────────────────────
        from sqlalchemy.pool import StaticPool
        return create_async_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=settings.debug,
        )

    # ── PostgreSQL (production) ─────────────────────────────────────────────
    # REQ: NFR-007 — pool_pre_ping ensures connections are live before use.
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        echo=settings.debug,
    )


engine = _build_engine()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def create_tables() -> None:
    """Create all tables on startup (dev/test only — use Alembic in prod)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_tables() -> None:
    """Drop all tables (test teardown only)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a DB session and commits/rolls back.
    REQ: COMP-02 — session-per-request pattern.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
