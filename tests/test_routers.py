"""
Integration tests for HTTP routers.

Maps directly to Gherkin scenarios in specs/url-shortener.yaml.

REQ: SC-001 — POST valid URL → 201
REQ: SC-002 — GET /{shortCode} → 302 (mocked; cache miss → DB)
REQ: SC-004 — POST invalid scheme → 422 INVALID_SCHEME
REQ: SC-005 — POST SSRF URL → 422 SSRF_BLOCKED
REQ: SC-006 — GET expired code → 410
REQ: SC-007 — POST duplicate alias → 409
REQ: SC-008 — 101st POST → 429 with Retry-After
REQ: SC-009 — DELETE own URL → 204; subsequent GET → 404
REQ: SC-010 — GET unknown code → 404
REQ: AC-010 — Missing API key → 401
"""

import pytest
from httpx import AsyncClient

from tests.conftest import TEST_API_KEY


class TestCreateUrl:
    """SC-001, SC-003, SC-004, SC-005, SC-007, SC-008"""

    @pytest.mark.asyncio
    async def test_create_valid_url_returns_201(self, client: AsyncClient) -> None:
        """REQ: SC-001, AC-001 — valid URL → 201 with shortUrl."""
        response = await client.post(
            "/api/v1/urls",
            json={"longUrl": "https://example.com/path?q=1"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 201
        body = response.json()
        assert "shortCode" in body
        assert body["shortUrl"].startswith("https://sho.rt/")
        assert body["longUrl"] == "https://example.com/path?q=1"
        assert "createdAt" in body

    @pytest.mark.asyncio
    async def test_create_with_custom_alias_returns_alias_as_code(self, client: AsyncClient) -> None:
        """REQ: SC-003, FR-007 — custom alias → shortCode equals alias."""
        response = await client.post(
            "/api/v1/urls",
            json={"longUrl": "https://shop.example.com/sale", "alias": "test-sale"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 201
        assert response.json()["shortCode"] == "test-sale"

    @pytest.mark.asyncio
    async def test_create_duplicate_alias_returns_409(self, client: AsyncClient) -> None:
        """REQ: SC-007, FR-007 — duplicate alias → 409 ALIAS_TAKEN."""
        payload = {"longUrl": "https://example.com", "alias": "conflict-alias"}
        await client.post("/api/v1/urls", json=payload, headers={"X-API-Key": TEST_API_KEY})

        response = await client.post(
            "/api/v1/urls", json=payload, headers={"X-API-Key": TEST_API_KEY}
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "ALIAS_TAKEN"

    @pytest.mark.asyncio
    async def test_create_javascript_scheme_returns_422(self, client: AsyncClient) -> None:
        """REQ: SC-004, AC-003, FR-003 — javascript: → 422 INVALID_SCHEME."""
        response = await client.post(
            "/api/v1/urls",
            json={"longUrl": "javascript:alert(1)"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_SCHEME"

    @pytest.mark.asyncio
    async def test_create_ssrf_url_returns_422(self, client: AsyncClient) -> None:
        """REQ: SC-005, AC-004, NFR-003 — private IP → 422 SSRF_BLOCKED."""
        response = await client.post(
            "/api/v1/urls",
            json={"longUrl": "http://192.168.1.1/admin"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "SSRF_BLOCKED"

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_401(self, client: AsyncClient) -> None:
        """REQ: AC-010, NFR-004 — no X-API-Key header → 401 UNAUTHORIZED."""
        response = await client.post(
            "/api/v1/urls",
            json={"longUrl": "https://example.com"},
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "UNAUTHORIZED"

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_returns_429(self, client: AsyncClient, monkeypatch) -> None:
        """
        REQ: SC-008, FR-004, AC-005 — 101st request → 429 with Retry-After.
        self-critique fix FIND-004: rate limit is per-user (not global).
        """
        from unittest.mock import AsyncMock, patch

        # Simulate counter = 101 (over the 100 req/min limit)
        with patch("src.middleware.rate_limiter.cache_incr", new_callable=AsyncMock, return_value=101):
            response = await client.post(
                "/api/v1/urls",
                json={"longUrl": "https://example.com/rl"},
                headers={"X-API-Key": TEST_API_KEY},
            )

        assert response.status_code == 429
        assert "Retry-After" in response.headers
        assert response.json()["detail"]["code"] == "RATE_LIMIT_EXCEEDED"


class TestRedirect:
    """SC-002, SC-006, SC-010"""

    @pytest.mark.asyncio
    async def test_redirect_active_url(self, client: AsyncClient) -> None:
        """REQ: SC-002, FR-002 — GET /{shortCode} → 302 Found with Location."""
        # Create a URL first
        create_resp = await client.post(
            "/api/v1/urls",
            json={"longUrl": "https://destination.example.com"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        short_code = create_resp.json()["shortCode"]

        response = await client.get(f"/{short_code}", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "https://destination.example.com"
        assert response.headers.get("cache-control") == "no-store"

    @pytest.mark.asyncio
    async def test_redirect_unknown_code_returns_404(self, client: AsyncClient) -> None:
        """REQ: SC-010 — unknown code → 404 SHORT_CODE_NOT_FOUND."""
        response = await client.get("/XXXXXXXX", follow_redirects=False)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "SHORT_CODE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_redirect_expired_url_returns_410(self, client: AsyncClient) -> None:
        """REQ: SC-006, FR-006 — expired URL → 410 URL_EXPIRED."""
        from datetime import timedelta, timezone, datetime
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        create_resp = await client.post(
            "/api/v1/urls",
            json={"longUrl": "https://expired.example.com", "expiresAt": past.isoformat()},
            headers={"X-API-Key": TEST_API_KEY},
        )
        short_code = create_resp.json()["shortCode"]

        response = await client.get(f"/{short_code}", follow_redirects=False)
        assert response.status_code == 410
        assert response.json()["detail"]["code"] == "URL_EXPIRED"


class TestDeleteUrl:
    """SC-009"""

    @pytest.mark.asyncio
    async def test_delete_own_url_returns_204(self, client: AsyncClient) -> None:
        """REQ: SC-009, FR-005 — owner delete → 204; subsequent GET → 404."""
        create_resp = await client.post(
            "/api/v1/urls",
            json={"longUrl": "https://to-be-gone.example.com"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        short_code = create_resp.json()["shortCode"]

        delete_resp = await client.delete(
            f"/api/v1/urls/{short_code}",
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert delete_resp.status_code == 204

        # Subsequent GET /{shortCode} must return 404 (REQ: FR-005)
        get_resp = await client.get(f"/{short_code}", follow_redirects=False)
        assert get_resp.status_code == 404


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client: AsyncClient) -> None:
        """REQ: NFR-007 — /health endpoint returns status field."""
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] in ("ok", "degraded")
        assert "version" in response.json()
