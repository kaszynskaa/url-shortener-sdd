"""
Cryptographic utilities.

REQ: NFR-005 — IP addresses MUST be hashed (SHA-256 + per-deployment salt)
               before persistence. Zero raw IPs in the CLICKS table.
"""

import hashlib

from src.config import settings


def hash_ip(raw_ip: str) -> str:
    """
    Hash a raw IP address with the deployment-level salt.

    REQ: NFR-005 — GDPR compliance: no PII stored in analytics.
    REQ: AC-009  — automated test asserts no raw IPs appear in CLICKS table.

    Uses SHA-256(salt + ":" + ip) — the salt prevents rainbow-table lookups
    across deployments.

    Args:
        raw_ip: The client IP as a string (IPv4 or IPv6).

    Returns:
        64-character hex digest.
    """
    payload = f"{settings.ip_hash_salt}:{raw_ip}".encode()
    return hashlib.sha256(payload).hexdigest()


def hash_api_key(raw_key: str) -> str:
    """
    Hash an API key before storing in the database.

    REQ: NFR-004 — API keys stored hashed; never in plaintext.

    Args:
        raw_key: The raw API key string.

    Returns:
        64-character hex digest.
    """
    return hashlib.sha256(raw_key.encode()).hexdigest()
