"""
Unit tests for URL validator.

REQ: FR-003  — scheme + SSRF validation
REQ: NFR-003 — all RFC 1918 / link-local / loopback / CGNAT ranges blocked
REQ: AC-003  — javascript: and file: return INVALID_SCHEME
REQ: AC-004  — private-range URLs return SSRF_BLOCKED
REQ: SC-004, SC-005 — Gherkin error-path scenarios covered
"""

import pytest

from src.services.validator import URLValidationError, validate_url


class TestSchemeValidation:
    """REQ: FR-003 — only http and https are allowed."""

    def test_valid_https_url_passes(self) -> None:
        """REQ: FR-003, SC-001 — https:// URL accepted."""
        result = validate_url("https://example.com/path?q=1")
        assert result == "https://example.com/path?q=1"

    def test_valid_http_url_passes(self) -> None:
        """REQ: FR-003 — http:// URL accepted."""
        result = validate_url("http://example.com")
        assert result == "http://example.com"

    def test_javascript_scheme_rejected(self) -> None:
        """REQ: FR-003, AC-003, SC-004 — javascript: rejected with INVALID_SCHEME."""
        with pytest.raises(URLValidationError) as exc_info:
            validate_url("javascript:alert(1)")
        assert exc_info.value.code == "INVALID_SCHEME"

    def test_file_scheme_rejected(self) -> None:
        """REQ: FR-003, AC-003 — file: rejected with INVALID_SCHEME."""
        with pytest.raises(URLValidationError) as exc_info:
            validate_url("file:///etc/passwd")
        assert exc_info.value.code == "INVALID_SCHEME"

    def test_ftp_scheme_rejected(self) -> None:
        """REQ: FR-003 — ftp: rejected."""
        with pytest.raises(URLValidationError) as exc_info:
            validate_url("ftp://files.example.com")
        assert exc_info.value.code == "INVALID_SCHEME"

    def test_data_uri_rejected(self) -> None:
        """REQ: FR-003 — data: URI rejected."""
        with pytest.raises(URLValidationError) as exc_info:
            validate_url("data:text/html,<h1>XSS</h1>")
        assert exc_info.value.code == "INVALID_SCHEME"


class TestSSRFBlocking:
    """
    REQ: NFR-003 — SSRF blocking for all private/reserved ranges.
    Self-critique fix FIND-001: validator now covers IPv6 and CGNAT.
    """

    @pytest.mark.parametrize("private_url", [
        "http://10.0.0.1/admin",          # RFC 1918 Class A
        "http://10.255.255.255/secret",   # RFC 1918 Class A upper bound
        "http://172.16.0.1",              # RFC 1918 Class B
        "http://172.31.255.255",          # RFC 1918 Class B upper bound
        "http://192.168.1.1/router",      # RFC 1918 Class C
        "http://127.0.0.1",               # loopback
        "http://127.255.255.255",         # loopback upper
        "http://169.254.0.1",             # link-local (APIPA)
        "http://169.254.169.254/metadata",# AWS metadata endpoint (famous SSRF target)
        "http://100.64.0.1",              # CGNAT (FIND-001 fix)
    ])
    def test_private_ip_urls_rejected(self, private_url: str) -> None:
        """REQ: NFR-003, AC-004, SC-005 — all private ranges return SSRF_BLOCKED."""
        with pytest.raises(URLValidationError) as exc_info:
            validate_url(private_url)
        assert exc_info.value.code == "SSRF_BLOCKED", (
            f"Expected SSRF_BLOCKED for {private_url}, got {exc_info.value.code}"
        )

    def test_public_ip_accepted(self) -> None:
        """REQ: NFR-003 — public IPs are not blocked."""
        # 8.8.8.8 is Google DNS — public, should pass
        result = validate_url("http://8.8.8.8")
        assert result == "http://8.8.8.8"

    def test_public_domain_accepted(self) -> None:
        """REQ: FR-003 — public domain resolves to non-private IP."""
        # Uses real DNS; acceptable in unit tests when no network mock
        result = validate_url("https://example.com")
        assert result == "https://example.com"

    def test_unresolvable_hostname_rejected(self) -> None:
        """REQ: NFR-003 — unresolvable hostname blocked (conservative stance)."""
        with pytest.raises(URLValidationError) as exc_info:
            validate_url("http://this-domain-absolutely-does-not-exist-xyz.invalid")
        assert exc_info.value.code == "SSRF_BLOCKED"


class TestURLFormat:
    """REQ: FR-003 — RFC 3986 structural validation."""

    def test_missing_hostname_rejected(self) -> None:
        """URL with no hostname is invalid."""
        with pytest.raises(URLValidationError) as exc_info:
            validate_url("https://")
        assert exc_info.value.code == "INVALID_URL"

    def test_url_with_path_and_query_accepted(self) -> None:
        """REQ: FR-001 — complex URLs with path, query, fragment are valid."""
        url = "https://example.com/a/b/c?foo=bar&baz=qux#section"
        assert validate_url(url) == url

    def test_url_with_port_accepted(self) -> None:
        """Non-standard port is valid for http/https."""
        url = "https://example.com:8443/api"
        assert validate_url(url) == url
