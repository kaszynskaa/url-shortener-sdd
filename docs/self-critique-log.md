# Self-Critique Log — Generate → Review → Fix Cycles

**Project:** URL Shortener Service  
**Spec:** SPEC-20260524-001 | **Plan:** ARCH-20260524-001  
**Review prompt:** `prompts/code-reviewer.yaml v1.0.0`

---

## Overview

The self-critique loop ran in two stages:

1. **Static code review** (`reviews/initial-code-review.json`) — reviewer applied against the initial implementation before any tests ran. Four findings identified.
2. **Test iteration** — five test-run / fix cycles to reach a green suite. Each cycle exposed a new class of failure.

---

## Stage 1 — Static Code Review (OWASP / code-reviewer.yaml)

**Review ID:** SEC-20260524-001  
**Files reviewed:** `validator.py`, `url_service.py`, `rate_limiter.py` (v0 — pre-fix)

### FIND-001 — High — A10 SSRF: Incomplete blocklist

**What the reviewer found:**

The initial `_BLOCKED_NETWORKS` list covered only four IPv4 ranges (RFC 1918 + loopback). Missing:

- `169.254.0.0/16` — AWS EC2 metadata endpoint (`169.254.169.254`) — the most exploited SSRF target
- `100.64.0.0/10` — CGNAT shared address space (RFC 6598)
- IPv6: `::1/128`, `fc00::/7`, `fe80::/10`, `::ffff:0:0/96`
- RFC 5737 documentation ranges (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`)

**Attack scenario:**  
Attacker submits `http://169.254.169.254/latest/meta-data/iam/security-credentials/` — bypasses the incomplete blocklist. Short link is created. Any visitor gets redirected to the AWS metadata endpoint and IAM credentials are exposed.

**Fix applied** (`src/services/validator.py`):

```python
# Before (4 entries)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
]

# After (14 entries — covers all private/reserved ranges)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),    # ← link-local / AWS metadata
    ipaddress.ip_network("100.64.0.0/10"),     # ← CGNAT (RFC 6598)
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:0:0/96"),
]
```

**Tests added:** `test_private_ip_urls_rejected` — parametrized over 10 ranges including `169.254.169.254`.

---

### FIND-002 — High — A01 IDOR: No ownership check in `delete_url`

**What the reviewer found:**

Initial `delete_url(db, short_code)` signature accepted no `user_id`. Any authenticated user could delete any other user's URL by knowing (or guessing) the short code.

**Attack scenario:**  
Attacker authenticates with a valid API key. Discovers competitor's campaign URL via enumeration. Calls `DELETE /api/v1/urls/{competitor_code}` — campaign link broken.

**Fix applied** (`src/services/url_service.py`):

```python
# Before — no ownership check
async def delete_url(db: AsyncSession, short_code: str) -> bool:
    url_obj = await db.scalar(select(URL).where(URL.short_code == short_code))
    if url_obj is None:
        return False
    url_obj.deleted_at = datetime.now(timezone.utc)

# After — FIND-002 fix
async def delete_url(
    db: AsyncSession,
    short_code: str,
    requesting_user_id: uuid.UUID,   # ← new parameter
) -> bool:
    url_obj = await db.scalar(...)
    if url_obj is None:
        return False
    if url_obj.user_id != requesting_user_id:   # ← ownership guard
        raise PermissionError("FORBIDDEN")
    url_obj.deleted_at = datetime.now(timezone.utc)
```

**Tests added:** `test_non_owner_cannot_delete` asserts `PermissionError` is raised.

---

### FIND-003 — Medium — A04 Insecure Design: ISO 8601 `ttl` field silently ignored

**What the reviewer found:**

`FR-009` specifies that callers may pass `ttl="P30D"` as an alternative to `expiresAt`. The initial implementation read `request.expires_at` only; `ttl` was never parsed. A user supplying `ttl` would receive a URL with no expiry — violating their intent.

**Fix applied** (`src/services/url_service.py`):

```python
def _parse_ttl(ttl_str: str) -> timedelta:
    patterns = [
        (r'^P(\d+)W$', lambda m: timedelta(weeks=int(m.group(1)))),
        (r'^P(\d+)D$', lambda m: timedelta(days=int(m.group(1)))),
        (r'^PT(\d+)H$', lambda m: timedelta(hours=int(m.group(1)))),
        (r'^PT(\d+)M$', lambda m: timedelta(minutes=int(m.group(1)))),
    ]
    ...

# In create_url:
expires_at = request.expires_at
if request.ttl and expires_at is None:
    delta = _parse_ttl(request.ttl)
    expires_at = datetime.now(timezone.utc) + delta
```

**Tests added:** `test_create_url_with_ttl_string` — verifies `P30D` produces ~30-day expiry.

---

### FIND-004 — Medium — A04 Insecure Design: Global rate-limit bucket

**What the reviewer found:**

Initial bucket key: `rl:{window_start}` — shared across all API keys. One attacker could exhaust the shared counter, denying all other users service for the remainder of the window.

**Fix applied** (`src/middleware/rate_limiter.py`):

```python
# Before — global bucket
bucket_key = f"rl:{window_start}"

# After — per-user isolation
bucket_key = f"rl:{current_user.id}:{window_start}"
```

**Tests added:** `test_rate_limit_exceeded_returns_429` mocks `cache_incr` returning 101, verifying 429 + `Retry-After` header.

---

### FIND-005 — Low — A09: SSRF events not written to audit log (deferred)

**What the reviewer found:**

`NFR-003` requires SSRF_BLOCKED events logged to `audit_log`. Validator raises the error without a DB write.

**Disposition:** Accepted as Phase 4 backlog. Events appear in structured application logs. `post-fix-review.json` records this as the only remaining item (`merge_decision: approve_with_comments`).

---

## Stage 2 — Test Iteration Log

### Iteration 1 — `ImportError: No module named 'asyncpg'`

**Cause:** `src/database.py` called `create_async_engine()` at module import time with the PostgreSQL URL. Importing the module without `asyncpg` installed raises `ModuleNotFoundError`.

**Fix:**
1. Created `conftest.py` (project root) — sets `DATABASE_URL=sqlite+aiosqlite:///:memory:` before any `src.*` imports via `os.environ.setdefault`.
2. Refactored `src/database.py` — `_build_engine()` factory checks the URL prefix; SQLite uses `StaticPool` (no `pool_size`/`max_overflow`); PostgreSQL uses the full production settings.

---

### Iteration 2 — `OSError: Multiple exceptions: Connect call failed (Redis)`

**Cause:** `src.middleware.rate_limiter` imports `cache_incr` at module level via `from src.cache import cache_incr`. Patching `src.cache.cache_incr` in the test fixture did not affect the already-bound local reference inside `rate_limiter`.

**Fix:**  
Changed all patches to target the import site, not the source module:

```python
# Wrong — patches the source, not the bound reference
patch("src.cache.cache_incr", ...)

# Correct — patches the local binding in rate_limiter
patch("src.middleware.rate_limiter.cache_incr", ...)
```

Applied same pattern for `url_service.cache_*`, `health.redis_ping`.

---

### Iteration 3 — `sqlite3.IntegrityError: UNIQUE constraint failed: users.api_key_hash`

**Cause:** `test_user` fixture called `await db_session.commit()`. Commit finalises the transaction; the subsequent `await session.rollback()` in `db_session` fixture has nothing to undo. The second test finds the user row still present and tries to insert the same `api_key_hash` again.

**Fix:**  
Changed `commit()` → `flush()` in `test_user`. The INSERT is sent within the open transaction and becomes visible to the session, but is rolled back at test teardown.

---

### Iteration 4 — SSRF false-positive on `*.example.com` subdomains

**Cause:** `destination.example.com`, `expired.example.com`, etc. have no DNS records. `socket.getaddrinfo` raises `gaierror`. The validator conservatively treats unresolvable hosts as private → `SSRF_BLOCKED` on test URLs.

**Fix:**  
Added `_mock_is_private_ip` in `tests/conftest.py`: resolves literal IPs against private ranges (keeping SSRF tests valid); returns `False` for hostnames (domain strings), bypassing DNS in the test environment.

```python
def _mock_is_private_ip(hostname: str) -> bool:
    try:
        addr = ipaddress.ip_address(hostname)   # literal IP
        return any(addr in net for net in _PRIVATE_NETS)
    except ValueError:
        return False   # hostname → allow in tests
```

Patched at: `src.services.validator._is_private_ip` in both `tests/conftest.py` (router tests) and `tests/test_url_service.py` (`stub_dns` autouse fixture).

Also fixed `src/routers/urls.py` — `except ValueError` block was using `str(exc)` as the error code. Fixed to `getattr(exc, "code", None)` so `URLValidationError.code` is correctly forwarded to the HTTP response.

---

### Iteration 5 — `TypeError: can't compare offset-naive and offset-aware datetimes`

**Cause:** SQLite returns `datetime` objects without timezone info for `TIMESTAMP` columns. The `is_expired` property on `URL` compared `self.expires_at` (naive) against `datetime.now(timezone.utc)` (aware).

**Fix** (`src/models.py`):

```python
@property
def is_expired(self) -> bool:
    if self.expires_at is None:
        return False
    from datetime import timezone
    now = datetime.now(timezone.utc)
    exp = self.expires_at
    if exp.tzinfo is None:          # normalise SQLite naive datetimes
        exp = exp.replace(tzinfo=timezone.utc)
    return now > exp
```

---

## Final Outcome

```
pytest tests/ → 47 passed in 0.19s
```

| File | Tests | All Pass |
|------|-------|----------|
| `tests/test_validator.py` | 12 | ✅ |
| `tests/test_url_service.py` | 16 | ✅ |
| `tests/test_routers.py` | 19 | ✅ |
| **Total** | **47** | **✅** |
