"""
Unit tests for URL service business logic.

REQ: FR-001  — create_url returns UrlResponse with shortCode
REQ: FR-002  — resolve_url returns longUrl on active URL
REQ: FR-005  — delete_url soft-deletes; subsequent resolve returns NOT_FOUND
REQ: FR-006  — resolve_url returns EXPIRED for expired URLs
REQ: FR-007  — custom alias; 409 on conflict
REQ: FR-009  — expiresAt / ttl parsing
REQ: AC-002  — ownership guard on delete (FIND-002 fix)
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import URL, User
from src.schemas import CreateUrlRequest
from src.services.url_service import ResolveStatus, delete_url, resolve_url
from src.services import url_service
from tests.conftest import TEST_API_KEY_HASH, _mock_is_private_ip


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="svc@test.com",
        api_key_hash=TEST_API_KEY_HASH,
        plan="free",
        is_active=True,
    )


def make_url(user_id: uuid.UUID, short_code: str = "abc12345", expires_at=None, deleted_at=None) -> URL:
    return URL(
        id=uuid.uuid4(),
        user_id=user_id,
        long_url="https://example.com/original",
        short_code=short_code,
        is_active=deleted_at is None and (expires_at is None or expires_at > datetime.now(timezone.utc)),
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        deleted_at=deleted_at,
    )


# ── create_url tests ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def stub_dns():
    """Patch _is_private_ip for all service-unit tests (no network needed)."""
    with patch("src.services.validator._is_private_ip", side_effect=_mock_is_private_ip):
        yield


class TestCreateUrl:
    """REQ: FR-001, FR-007, FR-009"""

    @pytest.mark.asyncio
    async def test_create_url_returns_url_response(self, db_session: AsyncSession, test_user: User) -> None:
        """REQ: FR-001 — valid URL → UrlResponse with shortCode."""
        req = CreateUrlRequest(longUrl="https://example.com/long-path")
        with patch("src.services.url_service.cache_set", new_callable=AsyncMock):
            result = await url_service.create_url(db_session, req, test_user.id)

        assert result.short_code
        assert len(result.short_code) == 8
        assert result.long_url == "https://example.com/long-path"
        assert result.short_url.startswith("https://sho.rt/")

    @pytest.mark.asyncio
    async def test_create_url_with_custom_alias(self, db_session: AsyncSession, test_user: User) -> None:
        """REQ: FR-007, SC-003 — custom alias used as short_code."""
        req = CreateUrlRequest(longUrl="https://example.com/shop", alias="my-shop")
        with patch("src.services.url_service.cache_set", new_callable=AsyncMock):
            result = await url_service.create_url(db_session, req, test_user.id)

        assert result.short_code == "my-shop"
        assert result.alias == "my-shop"

    @pytest.mark.asyncio
    async def test_create_url_duplicate_alias_raises(self, db_session: AsyncSession, test_user: User) -> None:
        """REQ: FR-007, SC-007 — duplicate alias raises ValueError('ALIAS_TAKEN')."""
        req = CreateUrlRequest(longUrl="https://first.example.com", alias="dup-alias")
        with patch("src.services.url_service.cache_set", new_callable=AsyncMock):
            await url_service.create_url(db_session, req, test_user.id)

        req2 = CreateUrlRequest(longUrl="https://second.example.com", alias="dup-alias")
        with pytest.raises(ValueError, match="ALIAS_TAKEN"):
            with patch("src.services.url_service.cache_set", new_callable=AsyncMock):
                await url_service.create_url(db_session, req2, test_user.id)

    @pytest.mark.asyncio
    async def test_create_url_with_expires_at(self, db_session: AsyncSession, test_user: User) -> None:
        """REQ: FR-009 — expiresAt persisted correctly."""
        future = datetime.now(timezone.utc) + timedelta(days=7)
        req = CreateUrlRequest(longUrl="https://example.com/expiry", expiresAt=future)
        with patch("src.services.url_service.cache_set", new_callable=AsyncMock):
            result = await url_service.create_url(db_session, req, test_user.id)

        assert result.expires_at is not None

    @pytest.mark.asyncio
    async def test_create_url_with_ttl_string(self, db_session: AsyncSession, test_user: User) -> None:
        """REQ: FR-009, FIND-003 fix — ISO 8601 ttl='P30D' parsed to 30-day expiry."""
        req = CreateUrlRequest(longUrl="https://example.com/ttl-test", ttl="P30D")
        with patch("src.services.url_service.cache_set", new_callable=AsyncMock):
            result = await url_service.create_url(db_session, req, test_user.id)

        assert result.expires_at is not None
        delta = result.expires_at - datetime.now(timezone.utc)
        assert 29 <= delta.days <= 30   # tolerance for test timing

    @pytest.mark.asyncio
    async def test_create_url_ssrf_rejected(self, db_session: AsyncSession, test_user: User) -> None:
        """REQ: NFR-003, AC-004, SC-005 — SSRF URL raises URLValidationError."""
        from src.services.validator import URLValidationError
        req = CreateUrlRequest(longUrl="http://192.168.1.1/admin")
        with pytest.raises(URLValidationError) as exc_info:
            await url_service.create_url(db_session, req, test_user.id)
        assert exc_info.value.code == "SSRF_BLOCKED"


# ── resolve_url tests ─────────────────────────────────────────────────────────

class TestResolveUrl:
    """REQ: FR-002, FR-006"""

    @pytest.mark.asyncio
    async def test_resolve_active_url(self, db_session: AsyncSession, test_user: User) -> None:
        """REQ: FR-002, SC-002 — active URL resolves to longUrl with OK status."""
        req = CreateUrlRequest(longUrl="https://destination.example.com")
        with patch("src.services.url_service.cache_set", new_callable=AsyncMock):
            created = await url_service.create_url(db_session, req, test_user.id)

        with patch("src.services.url_service.cache_get", new_callable=AsyncMock, return_value=None):
            result = await resolve_url(db_session, created.short_code)

        assert result.status == ResolveStatus.OK
        assert result.long_url == "https://destination.example.com"

    @pytest.mark.asyncio
    async def test_resolve_unknown_code_returns_not_found(self, db_session: AsyncSession) -> None:
        """REQ: SC-010 — unknown short code → NOT_FOUND."""
        with patch("src.services.url_service.cache_get", new_callable=AsyncMock, return_value=None):
            result = await resolve_url(db_session, "XXXXXXXX")
        assert result.status == ResolveStatus.NOT_FOUND

    @pytest.mark.asyncio
    async def test_resolve_expired_url_returns_expired(self, db_session: AsyncSession, test_user: User) -> None:
        """REQ: FR-006, SC-006 — expired URL resolves with EXPIRED status."""
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        req = CreateUrlRequest(longUrl="https://expired.example.com", expiresAt=past)
        with patch("src.services.url_service.cache_set", new_callable=AsyncMock):
            created = await url_service.create_url(db_session, req, test_user.id)

        with patch("src.services.url_service.cache_get", new_callable=AsyncMock, return_value=None):
            result = await resolve_url(db_session, created.short_code)

        assert result.status == ResolveStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_resolve_deleted_url_returns_deleted(self, db_session: AsyncSession, test_user: User) -> None:
        """REQ: FR-005, SC-009 — deleted URL resolves with DELETED status."""
        req = CreateUrlRequest(longUrl="https://to-delete.example.com")
        with patch("src.services.url_service.cache_set", new_callable=AsyncMock):
            created = await url_service.create_url(db_session, req, test_user.id)

        with patch("src.services.url_service.cache_delete", new_callable=AsyncMock):
            await delete_url(db_session, created.short_code, test_user.id)

        with patch("src.services.url_service.cache_get", new_callable=AsyncMock, return_value=None):
            result = await resolve_url(db_session, created.short_code)

        assert result.status == ResolveStatus.DELETED


# ── delete_url tests ──────────────────────────────────────────────────────────

class TestDeleteUrl:
    """REQ: FR-005, FIND-002 fix (ownership check)"""

    @pytest.mark.asyncio
    async def test_owner_can_delete(self, db_session: AsyncSession, test_user: User) -> None:
        """REQ: FR-005, SC-009 — owner deletes successfully, returns True."""
        req = CreateUrlRequest(longUrl="https://to-be-deleted.example.com")
        with patch("src.services.url_service.cache_set", new_callable=AsyncMock):
            created = await url_service.create_url(db_session, req, test_user.id)

        with patch("src.services.url_service.cache_delete", new_callable=AsyncMock):
            success = await delete_url(db_session, created.short_code, test_user.id)

        assert success is True

    @pytest.mark.asyncio
    async def test_non_owner_cannot_delete(self, db_session: AsyncSession, test_user: User) -> None:
        """
        REQ: FR-005, FIND-002 fix, AC-002 — non-owner raises PermissionError.
        Self-critique fix: initial version had no ownership check.
        """
        req = CreateUrlRequest(longUrl="https://protected.example.com")
        with patch("src.services.url_service.cache_set", new_callable=AsyncMock):
            created = await url_service.create_url(db_session, req, test_user.id)

        different_user_id = uuid.uuid4()  # Different user
        with pytest.raises(PermissionError):
            await delete_url(db_session, created.short_code, different_user_id)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self, db_session: AsyncSession) -> None:
        """REQ: FR-005 — deleting unknown code returns False (caller returns 404)."""
        with patch("src.services.url_service.cache_delete", new_callable=AsyncMock):
            result = await delete_url(db_session, "NOTEXIST", uuid.uuid4())
        assert result is False
