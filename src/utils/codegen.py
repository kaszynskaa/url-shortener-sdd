"""
Short-code generator.

REQ: FR-001  — Returns a unique 6–10 char alphanumeric code.
REQ: ADR-04  — Uses secrets.choice over 62-char alphabet (no sequential IDs).
               62^8 ≈ 218 trillion combinations; collision probability negligible.
"""

import secrets
import string

from src.config import settings

# 62-char URL-safe alphabet (a-z, A-Z, 0-9)
# REQ: ADR-04 — excludes hyphens/underscores to keep codes visually clean
_ALPHABET = string.ascii_letters + string.digits


def generate_short_code(length: int | None = None) -> str:
    """
    Generate a cryptographically random short code.

    REQ: FR-001  — alphanumeric, 6–10 chars (default from settings).
    REQ: ADR-04  — secrets.choice is CSPRNG-backed, not predictable.

    Args:
        length: Override default code length from settings.

    Returns:
        A random string of `length` characters from [a-zA-Z0-9].
    """
    n = length or settings.short_code_length
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))
