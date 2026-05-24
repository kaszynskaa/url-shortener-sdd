"""
Shared pytest fixtures.

REQ: ARCH-20260524-001 Phase 5 — test infrastructure.

Uses in-memory SQLite (aiosqlite) and mocked Redis so no external services
are required. The root conftest.py (project root) sets DATABASE_URL to the
SQLite URL before any src.* imports so the PostgreSQL engine is never built.

Patching strategy — Python's `from module import name` creates a LOCAL binding.
We must patch at each IMPORT SITE, not the original `src.cache` module:
  rate_limiter imports cache_incr  → patch src.middleware.rate_limiter.cache_incr
  url_service  imports cache_*     → patch src.services.url_service.cache_*
  health.py    imports ping        → patch src.routers.health.redis_ping
"""

import ipaddress
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.database import get_db
from src.main import create_app
from src.models import Base, User
from src.utils.crypto import hash_api_key

# ── Dedicated test engine ─────────────────────────────────────────────────────
test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _mock_is_private_ip(hostname: str) -> bool:
    """
    Test-environment DNS stub for src.services.validator._is_private_ip.

    Literal private IPs are still blocked (SSRF tests need this).
    Hostnames (e.g. *.example.com) are allowed through — they don't have
    DNS records in CI so getaddrinfo raises gaierror, which the real
    implementation treats as private. Tests should not depend on live DNS.
    """
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _PRIVATE_NETS)
    except ValueError:
        # hostname string (not a bare IP) → allow in tests
        return False


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    """Create all tables once per session, drop after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Per-test session that rolls back for isolation."""
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


# ── Test credentials ──────────────────────────────────────────────────────────
TEST_API_KEY = "test-api-key-12345"
TEST_API_KEY_HASH = hash_api_key(TEST_API_KEY)


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Insert a test user with a known API key hash."""
    import uuid
    user = User(
        id=uuid.uuid4(),
        email="test@example.com",
        api_key_hash=TEST_API_KEY_HASH,
        plan="free",
        is_active=True,
    )
    db_session.add(user)
    # flush (not commit) → visible within session, rolled back after each test
    await db_session.flush()
    return user


# ── HTTP test client ──────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    test_user: User,  # ensures user row exists before any request
) -> AsyncGenerator[AsyncClient, None]:
    """
    Full HTTP test client with:
      - DB dependency overridden to use the SQLite test session
      - All Redis / cache operations mocked at each import site
      - Lifespan startup mocked (tables already exist; Redis not needed)
    """
    _ = test_user  # used as fixture dep, not in body

    app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with (
        # ── Lifespan mocks ────────────────────────────────────────────────
        patch("src.main.create_tables", new_callable=AsyncMock),
        patch("src.main.get_redis",    new_callable=AsyncMock),
        patch("src.main.close_redis",  new_callable=AsyncMock),

        # ── Cache mocks at each import site ───────────────────────────────
        # rate_limiter.py: from src.cache import cache_incr
        patch("src.middleware.rate_limiter.cache_incr",
              new_callable=AsyncMock, return_value=1),

        # url_service.py: from src.cache import cache_set/get/delete
        patch("src.services.url_service.cache_set",   new_callable=AsyncMock),
        patch("src.services.url_service.cache_get",   new_callable=AsyncMock, return_value=None),
        patch("src.services.url_service.cache_delete", new_callable=AsyncMock),

        # health.py: from src.cache import ping as redis_ping
        patch("src.routers.health.redis_ping", new_callable=AsyncMock, return_value=True),

        # redirect.py — analytics is a fire-and-forget background task;
        # mock record_click so it doesn't open a second DB session
        patch("src.routers.redirect.analytics_service.record_click",
              new_callable=AsyncMock),

        # DNS stub: allow *.example.com hostnames through in test environment
        patch("src.services.validator._is_private_ip", side_effect=_mock_is_private_ip),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
