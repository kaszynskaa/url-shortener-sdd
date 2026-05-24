"""
URL validation — scheme check and SSRF blocking.

REQ: FR-003  — SHALL validate that longUrl is syntactically valid (RFC 3986)
               and uses http or https scheme.
REQ: NFR-003 — MUST block RFC 1918, loopback, link-local, and IPv6 private
               ranges to prevent SSRF. Zero SSRF findings in DAST scan.
REQ: AC-004  — SSRF attempts MUST be logged to the security audit log.

Self-critique fix (FIND-001 from initial-code-review.json):
  Initial version only checked IPv4 RFC-1918. Reviewer flagged missing:
  - IPv6 ::1, fc00::/7, fe80::/10
  - 169.254.0.0/16 (link-local)
  - 100.64.0.0/10 (CGNAT / shared address space)
  All now covered via the _BLOCKED_NETWORKS list.
"""

import ipaddress
import socket
from urllib.parse import urlparse

# ── Blocked network ranges  (REQ: NFR-003) ────────────────────────────────
# Covers: RFC 1918, loopback, link-local, CGNAT, documentation, IPv6 private.
_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # IPv4
    ipaddress.ip_network("10.0.0.0/8"),         # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),       # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),      # RFC 1918
    ipaddress.ip_network("127.0.0.0/8"),         # loopback
    ipaddress.ip_network("169.254.0.0/16"),      # link-local (APIPA)
    ipaddress.ip_network("100.64.0.0/10"),       # CGNAT (RFC 6598)
    ipaddress.ip_network("192.0.2.0/24"),        # TEST-NET-1 (RFC 5737)
    ipaddress.ip_network("198.51.100.0/24"),     # TEST-NET-2 (RFC 5737)
    ipaddress.ip_network("203.0.113.0/24"),      # TEST-NET-3 (RFC 5737)
    ipaddress.ip_network("0.0.0.0/8"),           # "this" network
    # IPv6
    ipaddress.ip_network("::1/128"),             # loopback
    ipaddress.ip_network("fc00::/7"),            # unique local (ULA)
    ipaddress.ip_network("fe80::/10"),           # link-local
    ipaddress.ip_network("::ffff:0:0/96"),       # IPv4-mapped
]

_ALLOWED_SCHEMES = {"http", "https"}


class URLValidationError(ValueError):
    """Raised when a submitted URL fails validation."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code          # Machine-readable code for ErrorResponse
        self.detail = detail


def _is_private_ip(hostname: str) -> bool:
    """
    Returns True if the hostname resolves to a blocked (private/internal) address.

    REQ: NFR-003 — resolves hostname to catch DNS-level SSRF.
    NOTE: DNS rebinding is a known limitation (ADR risk register). We resolve
    once at submission time; we do NOT re-validate at redirect time.
    """
    try:
        # getaddrinfo returns all addresses; check every one
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # Unresolvable hostname — reject to be safe
        return True

    for *_, sockaddr in infos:
        addr_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(addr_str)
        except ValueError:
            continue
        for network in _BLOCKED_NETWORKS:
            if addr in network:
                return True
    return False


def validate_url(raw_url: str) -> str:
    """
    Validate a URL for scheme and SSRF safety.

    REQ: FR-003  — Must accept only http/https, reject others.
    REQ: NFR-003 — Must block private/internal IP ranges.

    Args:
        raw_url: The URL string submitted by the user.

    Returns:
        The validated URL string (unchanged).

    Raises:
        URLValidationError: with code INVALID_SCHEME or SSRF_BLOCKED.
    """
    # ── 1. Parse ───────────────────────────────────────────────────────────
    try:
        parsed = urlparse(raw_url)
    except Exception as exc:
        raise URLValidationError(
            code="INVALID_URL",
            detail=f"URL could not be parsed: {exc}",
        ) from exc

    # ── 2. Scheme check  (REQ: FR-003) ────────────────────────────────────
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise URLValidationError(
            code="INVALID_SCHEME",
            detail=(
                f"URL scheme '{parsed.scheme}' is not allowed. "
                "Only http and https are accepted."
            ),
        )

    # ── 3. Hostname required ───────────────────────────────────────────────
    hostname = parsed.hostname
    if not hostname:
        raise URLValidationError(
            code="INVALID_URL",
            detail="URL must contain a valid hostname.",
        )

    # ── 4. SSRF check  (REQ: NFR-003, self-critique fix: FIND-001) ────────
    if _is_private_ip(hostname):
        raise URLValidationError(
            code="SSRF_BLOCKED",
            detail=(
                f"The hostname '{hostname}' resolves to a private or reserved "
                "IP address and cannot be shortened."
            ),
        )

    return raw_url
