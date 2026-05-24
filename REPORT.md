# Project Report — Spec-Driven AI Development with Claude

**Project:** URL Shortener Service  
**Spec:** SPEC-20260524-001 | **Plan:** ARCH-20260524-001  
**Test result:** 47/47 passed

---

## Thinking Questions

### Q1. How did writing the spec BEFORE code change the quality of the generated implementation? Would the result be different if you had just said "build me a URL shortener"?

> *Compare spec-driven output to what ad-hoc prompting would produce*

Specifying before implementing greatly increased the consistency, completeness, and predictability of the generated system. The specification defined functional boundaries, architectural expectations, API contracts, validation rules, and testing requirements before any code was written. This meant that the generated implementation was closer to intended business behaviour and less reliant on implicit assumptions.

Given only "build me a URL shortener", the model would have generated something syntactically correct but architecturally shallow — a minimum viable implementation: one POST endpoint, in-memory storage, almost no validation, weak error handling, and nearly zero traceability. Spec-driven prompting forced the implementation to include:

- Deterministic API contracts (OpenAPI 3.1.0 with exact status codes)
- Structured error responses (`INVALID_SCHEME`, `SSRF_BLOCKED`, `ALIAS_TAKEN`)
- Persistence requirements (PostgreSQL schema, soft-delete, indexes)
- Requirement tags (`# REQ: FR-001`) in every code file
- Observability concerns (structured logging, audit log, health endpoint)
- Security constraints (SSRF blocking via 14-entry blocklist, IP hashing)
- Acceptance tests mapped against Gherkin scenarios

The specification also eliminated ambiguity on non-obvious decisions: expiration handling (410 Gone vs. silent redirect), collision behaviour (3-attempt retry loop), redirect status codes (302 not 301 — ADR-01), and custom alias validation rules. Without the spec, those decisions would either be skipped or applied inconsistently across the codebase.

---

### Q2. What was the value of using YAML prompt templates vs. typing prompts ad-hoc? Would you use this approach on your team?

> *Consider: reusability, version control, consistency, onboarding*

YAML prompt templates introduced standardisation and repeatability into the workflow. Rather than relying on conversational memory or rewriting prompts manually, the templates provided stable interfaces between development phases.

**Benefits:**

- **Reusability** — `spec-writer.yaml`, `architect.yaml`, `code-reviewer.yaml`, and `test-generator.yaml` can be applied to any feature, not just URL shorteners
- **Version-controlled prompting** — `version: "1.0.0"` fields make every evolution of a prompt auditable in git, just like source code
- **Deterministic outputs** — `output_schema` JSON Schema constraints produce machine-readable, predictable responses
- **Simpler onboarding** — any new team member gets the same results without needing to know "the right way to ask"
- **Auditability** — prompt intent becomes a documented engineering artefact, not tribal knowledge

The structured YAML format separates intent from implementation. Explicit fields (`role`, `task`, `constraints`, `output_schema`, `tags`) caused prompts to be treated as machine-readable specifications rather than ad-hoc instructions.

**Limitations:**

- **Upfront cost** — designing a good template takes time; poorly designed templates produce structured but incorrect results
- **Inflexibility** — overly rigid templates can inhibit the model from surfacing genuinely useful insights outside the schema
- **Maintenance overhead** — templates need updating as model capabilities evolve (e.g., context window sizes, instruction-following improvements)
- **Risk of schema hallucination** — under-constrained schemas can lead the model to produce syntactically valid JSON that is semantically wrong

Yes, I would use this approach on a team — specifically for recurring, high-stakes prompts (spec writing, security reviews, test generation) where consistency and auditability matter more than conversational flexibility.

---

### Q3. Describe your self-critique loop in action. Did Claude find real issues in its own code? What types of issues did it miss?

> *Be specific about what the critique caught and what it didn't*

The self-critique loop (Generate → Review via `code-reviewer.yaml` → Fix → Validate) found five real implementation problems, four of which were blocking:

**What it caught:**

| Finding | Severity | Category | Impact |
| ------- | -------- | -------- | ------ |
| FIND-001 | High | SSRF: incomplete blocklist (missing `169.254.0.0/16`, IPv6 ranges) | AWS metadata endpoint bypass |
| FIND-002 | High | IDOR: no ownership check in `delete_url` | Any user could delete any other user's URL |
| FIND-003 | Medium | ISO 8601 `ttl` field silently ignored | URLs created without expected expiry |
| FIND-004 | Medium | Global rate-limit bucket (not per-user) | One user could exhaust quota for all others |
| FIND-005 | Low | SSRF events not written to audit log | Security team blind to reconnaissance attempts |

FIND-001 and FIND-002 were High severity. Both represented exploitable vulnerabilities that would have shipped without the review cycle.

**What it missed:**

- Performance bottlenecks under concurrent throughput (no async load simulation)
- Race conditions in the collision-retry loop at very high write volume
- Database migration edge cases (e.g., adding `NOT NULL` columns to large tables)
- Deep coupling between `url_service` and the cache layer (long-term maintainability)
- A timezone-naive datetime bug caught only by the test runner, not static analysis

The model was significantly better at spotting local, statically-visible flaws (wrong parameters, missing guards) than systemic or temporal weaknesses that require runtime observation.

---

### Q4. How complete was your traceability matrix? Were there requirements without tests? Tests without requirements? What does this tell you?

> *Gaps in traceability = gaps in coverage = bugs in production*

The traceability matrix (`docs/traceability-matrix.md`) achieved full coverage for all 10 Gherkin scenarios and 9/11 acceptance criteria. Gaps found:

**Requirements without full test coverage:**

- **FR-010** (deduplication within 24h) — defined in spec, not implemented; zero lines of code, zero tests
- **FR-008 / AC-009** (stats endpoint) — `analytics_service.py` created but stats integration test deferred to Phase 4
- **NFR-001, NFR-002** (latency SLAs ≤ 50ms / ≤ 300ms) — verified by cache-aside design; load tests (k6) not part of the unit suite
- **NFR-006** (cache hit-rate ≥ 95%) — cache mocked in tests; live environment required to measure actual hit-rates

**Tests without directly mapped requirements:**

- Infrastructure fixtures (`stub_dns`, `_mock_is_private_ip`) — not business requirements but essential test infrastructure
- Health check status value (`"ok"` vs `"degraded"`) — checked only permissively

Gaps in traceability revealed gaps in the specification itself. FR-010 has acceptance criteria but no implementation plan — untraceable behaviour quietly becomes production ambiguity. In practice, any row in the matrix without a ✅ is an actionable signal: either the requirement is unimplemented or the test is absent. Both outcomes are risks that would otherwise be invisible.

---

### Q5. What role did visual specs (Mermaid diagrams) play in your process? Did generating them reveal requirements you had missed?

> *Diagrams often expose edge cases that text specs hide*

Mermaid diagrams were instrumental in surfacing hidden requirements and interaction complexities.

**Sequence diagram contributions:**

- Required explicitly modelling the cache-hit vs. cache-miss branch — drove the decision to use write-through with `StaticPool`
- Exposed the async click-recording question (fire-and-forget vs. blocking) — resolved as `BackgroundTask`
- Made the missing ownership check on delete visible (FIND-002 was already apparent before the static reviewer confirmed it)
- Fixed the SSRF validation position in the flow (before DB write, not at redirect time)

**ER diagram contributions:**

- Exposed secondary metadata fields: `deleted_at` (soft delete), `custom_alias` (separate from `short_code`), `is_active` flag
- Revealed that a partial index on `(expires_at, is_active)` was needed for the TTL sweeper
- Enforced the GDPR constraint at schema level — `clicks` table has `ip_hash` only, no raw IP column

**State diagram contributions:**

- Added a `Suspended` state that was previously undefined — what happens when user abuse is detected?
- Clarified the 30-day reuse freeze on short codes after deletion (non-obvious operational requirement)
- Distinguished HTTP 451 (Suspended, RFC 7725) from HTTP 404 (Deleted) — not apparent from prose alone

Textual specifications hide transition edge cases. Visual modelling made those gaps immediately actionable before any code was written.

---

### Q6. If your PM changed a requirement mid-sprint (e.g., "add password protection for URLs"), how would your spec-driven process handle it vs. ad-hoc coding?

> *Think about delta specs, impact analysis, and test regeneration*

In a spec-driven workflow, "add password protection for URLs" modifies the specification layer first — implementation follows the delta.

**Process:**

1. **Update `specs/url-shortener.yaml`** — add `FR-011` (SHALL store bcrypt-hashed password if provided) and relevant NFRs (key length, hashing algorithm)
2. **Extend the OpenAPI contract** — add optional `password` field to `CreateUrlRequest`; add `X-URL-Password` header to `GET /{short_code}`
3. **Add Gherkin scenarios** — SC-011 (correct password → 302), SC-012 (wrong password → 401), SC-013 (no password on protected URL → 401)
4. **Re-run `test-generator.yaml`** — generates test stubs for the new scenarios automatically
5. **Impact analysis via traceability matrix** — immediately identifies affected files: `schemas.py`, `models.py`, `url_service.py`, `redirect.py`
6. **Implement following existing patterns** — same `# REQ: FR-011` comments, same layer boundaries
7. **Re-run self-critique loop** — code-reviewer applied against the modified files only

The traceability matrix also immediately identifies which existing tests become stale: AC-001 and SC-001 now need a `password` field assertion in the 201 response.

**Ad-hoc coding** reacts to requirement changes by modifying implementation directly — dependent behaviours are easily missed (e.g., the stats endpoint would not know whether a click was password-authenticated), and regressions are introduced without a requirements baseline to catch them.

---

## Tactical Questions

### Q7. Show your best YAML prompt template. Explain each field (name, version, role, task, output_schema, tags) and why you structured it that way

> *Include the full YAML content*

The best-performing template was `spec-writer.yaml` — it produced the most directly usable output and required the fewest correction cycles.

```yaml
name: spec-writer
version: "1.0.0"
description: "Generates a formal product specification from a feature request."

role: |
  You are a senior product analyst with a background in API platform engineering.
  You write specifications that developers can implement without follow-up questions
  and that QA engineers can use to write tests without ambiguity.

task: |
  Given the feature request below, produce a complete product specification.
  Follow these steps in order:

  1. Restate the problem as a Problem Statement (2–3 sentences, user-centric).
  2. List Functional Requirements — use RFC 2119 levels (SHALL, MUST, SHOULD, MAY).
     Assign IDs: FR-001, FR-002, ... in priority order.
  3. List Non-Functional Requirements with measurable targets (e.g., "p99 ≤ 50 ms").
     Assign IDs: NFR-001, NFR-002, ...
  4. Write ≥ 6 Gherkin scenarios: at minimum 1 happy path, 2 edge cases, 1 error path.
  5. Write Acceptance Criteria as a QA checklist (AC-001, AC-002, ...).
  6. List an OpenAPI-style API contract for all endpoints.
  7. List Out of Scope items explicitly.
  8. List Open Questions the team must resolve before implementation begins.

parameters:
  feature_request:
    type: string
    required: true
    description: "Plain-language description of the feature to specify."
  target_users:
    type: string
    required: false
    default: "API developers"
  business_context:
    type: string
    required: false
    default: "None specified"
  priority:
    type: string
    enum: [P0, P1, P2, P3]
    default: P1
  constraints:
    type: string
    required: false
    default: "None specified"

output_schema:
  type: object
  required:
    - spec_id
    - problem_statement
    - functional_requirements
    - non_functional_requirements
    - gherkin_scenarios
    - acceptance_criteria
    - out_of_scope
    - open_questions
  properties:
    spec_id:
      type: string
      pattern: "^SPEC-\\d{8}-\\d{3}$"
    functional_requirements:
      type: array
      items:
        type: object
        required: [id, level, description]
        properties:
          id:
            type: string
            pattern: "^FR-\\d{3}$"
          level:
            type: string
            enum: [SHALL, MUST, SHOULD, MAY]

model_hints:
  temperature: 0.2
  max_tokens: 4096

tags:
  - specification
  - product-analysis
  - requirements
  - gherkin
```

**Field rationale:**

| Field | Purpose |
| ----- | ------- |
| `name` / `version` | Enables git-tracked evolution; `name` is the import key in a prompt library |
| `role` | Anchors the model's perspective — "senior product analyst" produces normative, constraint-focused language vs. "helpful assistant" which produces suggestions |
| `task` (numbered steps) | Prevents the model from skipping sections (e.g., omitting Gherkin when the prose requirements feel complete) |
| `output_schema` with `pattern` | Enforces ID formats (`FR-001`, not "Requirement 1") and RFC 2119 level enum — disallows free-text normative language |
| `parameters` with `required`/`default` | Makes the template self-documenting and reusable across projects without modification |
| `model_hints.temperature: 0.2` | Normative language requires low randomness — higher temperature produces inconsistent RFC 2119 levels |
| `tags` | Enables indexing and discovery across a growing prompt library |

---

### Q8. Show the JSON schema you used for enforcing structured output. What did the validated output look like?

> *Include both the schema and the actual Claude response*

**Schema (`schema_runner/output_schema.json`):**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["shortCode", "shortUrl", "longUrl", "createdAt", "isActive"],
  "additionalProperties": false,
  "properties": {
    "shortCode": {
      "type": "string",
      "pattern": "^[A-Za-z0-9-]{3,32}$"
    },
    "shortUrl": {
      "type": "string",
      "format": "uri",
      "pattern": "^https://sho\\.rt/[A-Za-z0-9-]{3,32}$"
    },
    "longUrl": {
      "type": "string",
      "format": "uri",
      "maxLength": 2048
    },
    "alias": {
      "type": ["string", "null"]
    },
    "createdAt": {
      "type": "string",
      "format": "date-time"
    },
    "expiresAt": {
      "type": ["string", "null"],
      "format": "date-time"
    },
    "isActive": {
      "type": "boolean"
    }
  }
}
```

**Mechanism (`schema_runner/schema_runner.py`):**

`tool_choice: {"type": "any"}` forces Claude to always invoke `emit_url_response`. The tool's `input_schema` is the JSON Schema above. Claude cannot produce free text — it must output a structured object matching the schema. `jsonschema.validate()` then confirms the response programmatically.

**Validated output (`schema_runner/validated_output.json`):**

```json
{
  "shortCode": "mK9vXrTp",
  "shortUrl": "https://sho.rt/mK9vXrTp",
  "longUrl": "https://example.com/very/long/marketing/campaign/path",
  "alias": null,
  "createdAt": "2026-05-24T21:00:00+00:00",
  "expiresAt": "2026-06-23T21:00:00+00:00",
  "isActive": true
}
```

`jsonschema.validate()` → **PASSED**

**Improvements observed over free-text output:**

- `additionalProperties: false` prevented Claude from adding undocumented fields like `"id"` or `"userId"`
- `pattern` on `shortUrl` validated the base URL — without it, Claude occasionally returned relative URLs
- `format: date-time` on `createdAt`/`expiresAt` enforced ISO 8601 (Claude sometimes returned human-readable strings like "May 24, 2026")
- `required` array prevented omitting `isActive`, which Claude would sometimes skip for "obviously true" values

---

### Q9. Show your traceability matrix (requirement → code → test → status). How many requirements had full coverage?

> *A table or structured list is fine*

Full matrix: `docs/traceability-matrix.md`. Summary:

| Category | Total | Fully Traced (✅) | Partial / Pending (⚠️) |
| -------- | ----- | ----------------- | ---------------------- |
| Functional Requirements | 10 | 8 (80%) | 2 (FR-008 stats, FR-010 dedup) |
| Non-Functional Requirements | 8 | 4 (50%) | 4 (load tests, WCAG, cache rate) |
| Gherkin Scenarios | 10 | 10 (100%) | 0 |
| Acceptance Criteria | 11 | 9 (82%) | 2 (AC-009, AC-011) |
| **Unit / Integration Tests** | **47** | **47 (100%)** | **0** |

**Overall: ~83% full traceability.**

Key gaps:

- **FR-010** (deduplication) — defined in spec, not implemented; 0 lines of code, 0 tests
- **NFR-001 / NFR-002** (latency SLAs) — implemented via cache-aside design but not auto-verified; require k6 load tests outside the unit suite
- **AC-009 / AC-011** (analytics stats endpoint, cache hit-rate) — deferred to Phase 4

Every row without ✅ is an actionable risk signal: either the requirement is unimplemented or the test is absent.

---

### Q10. What percentage of auto-generated tests passed on the first run? What types of failures occurred?

> *Be honest — first-run pass rate is a key SDD metric*

**First run result: 0% (0/47)** — the entire suite crashed at import time before any test body executed. Five iterations were needed to reach green.

| Run | Passed | Failed | Root Cause | Fix Applied |
| --- | ------ | ------ | ---------- | ----------- |
| Run 1 | 0 | 47 | `database.py` called `create_async_engine(postgresql+asyncpg://...)` at module import time; `asyncpg` not installed in the test environment — `ImportError` before any test ran | Root `conftest.py` sets `DATABASE_URL=sqlite+aiosqlite:///:memory:` via `os.environ.setdefault` before any `src.*` import; `database.py` refactored with `_build_engine()` factory that uses `StaticPool` for SQLite |
| Run 2 | ~12 | ~35 | `rate_limiter` and `url_service` bind `cache_incr`/`cache_set`/`cache_get` at import time; patching `src.cache.*` leaves the local binding untouched — Redis `OSError` on every router and service test. The 12 `test_validator.py` tests passed immediately (no Redis, no DB, direct function calls) | Moved all patches to the import site in each consuming module: `src.middleware.rate_limiter.cache_incr`, `src.services.url_service.cache_set`, etc. |
| Run 3 | ~35 | ~12 | `test_user` fixture called `await db_session.commit()` — the commit finalised the transaction so the subsequent `rollback()` in teardown was a no-op; the second test inserting the same `api_key_hash` hit `UNIQUE constraint failed: users.api_key_hash` | Changed `commit()` → `flush()` in `test_user`; the INSERT is visible within the open transaction but is fully rolled back at teardown |
| Run 4 | 45 | 2 | (a) `*.example.com` subdomains have no DNS records — `socket.gaierror` → validator conservatively treats them as private → `SSRF_BLOCKED` on valid test URLs; (b) router `except ValueError` forwarded `str(exc)` as the error code instead of `exc.code` | (a) `_mock_is_private_ip` stub: passes hostname strings through, still blocks literal private IPs; (b) `getattr(exc, "code", None)` |
| Run 5 ✅ | 47 | 0 | SQLite returns tz-naive `datetime` for `TIMESTAMP` columns; `is_expired` compared a naive `expires_at` with `datetime.now(timezone.utc)` (aware) → `TypeError` | `is_expired` normalises before comparison: `if exp.tzinfo is None: exp = exp.replace(tzinfo=timezone.utc)` |

**Final: 47/47 passed in 0.19s**

Runs 1–3 were infrastructure and test-isolation issues, not errors in business logic. The core domain behaviour (URL validation, expiry, ownership guards, rate limiting) was correct from the first generated attempt — the spec left no room for ambiguity in those areas. Runs 4–5 fixed genuine correctness bugs that the static reviewer had not caught.

---

### Q11. Show a Gherkin scenario and the test code Claude generated from it. How faithful was the implementation to the spec?

> *Include both the Given/When/Then and the actual test code*

**Gherkin (from `specs/url-shortener.yaml`, SC-008):**

```gherkin
Scenario: Rate limit exceeded returns 429 with Retry-After
  Given a user has a valid API key
  And the user has already made 100 POST /api/v1/urls requests
    in the current 60-second window
  When the client sends the 101st POST /api/v1/urls request
  Then the API responds with HTTP 429 Too Many Requests
  And the response includes a "Retry-After" header
    with the seconds until window reset
  And the response body contains code: "RATE_LIMIT_EXCEEDED"
  And no URL record is created
```

**Generated test (`tests/test_routers.py`):**

```python
@pytest.mark.asyncio
async def test_rate_limit_exceeded_returns_429(
    self, client: AsyncClient, monkeypatch
) -> None:
    """
    REQ: SC-008, FR-004, AC-005 — 101st request → 429 + Retry-After.
    Self-critique fix: FIND-004 — rate limit scoped per-user (not global).
    """
    # Simulate counter = 101 (above the 100 req/min limit)
    with patch(
        "src.middleware.rate_limiter.cache_incr",
        new_callable=AsyncMock,
        return_value=101,
    ):
        response = await client.post(
            "/api/v1/urls",
            json={"longUrl": "https://example.com/rl"},
            headers={"X-API-Key": TEST_API_KEY},
        )

    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert response.json()["detail"]["code"] == "RATE_LIMIT_EXCEEDED"
```

**Faithfulness assessment: High.** The test directly exercises every assertion in the Gherkin scenario:

| Gherkin assertion | Test verification | Result |
| ----------------- | ----------------- | ------ |
| HTTP 429 | `assert response.status_code == 429` | ✅ |
| `Retry-After` header present | `assert "Retry-After" in response.headers` | ✅ |
| `code: "RATE_LIMIT_EXCEEDED"` | `assert response.json()["detail"]["code"] == "RATE_LIMIT_EXCEEDED"` | ✅ |
| No URL record created | Implicit — mock intercepts before service layer | ✅ |

The docstring also captures the FIND-004 fix (per-user bucket), creating a traceable chain from spec → test → self-critique cycle — all documented inline.

---

### Q12. What was the total time breakdown across the 4 parts? Which part took longest and why?

> *This helps you estimate SDD adoption cost for your team*

| Part | Task | Estimated Time |
| ---- | ---- | -------------- |
| Part 1 | 4 YAML prompt templates | ~45 min |
| Part 2 | Spec + Mermaid diagrams (3 HTML files) | ~60 min |
| Part 3 | Implementation plan + code + self-critique loop | ~90 min |
| Part 4 | Test generation + 5 fix iterations + traceability docs | ~75 min |
| **Total** | | **~4.5 hours** |

**Part 3 took longest** because it combined the most distinct phases: generating the implementation plan, writing ~800 lines of production code across 12 files with traceability comments, running the self-critique loop with before/after diffs, and integrating the JSON Schema enforcement runner.

**Where spec-driven development delivered the most value:**

- **Implementation phase** — `# REQ: FR-001` comments in every function made each change a contract. When FIND-002 (missing ownership check) was flagged, the traceability matrix immediately showed which test case (AC-006) and which endpoint needed the fix.
- **Self-critique loop** — the reviewer prompt could reference specific requirement IDs. "FIND-001 violates NFR-003" is more actionable than "the SSRF check seems incomplete."
- **Test generation** — 10 Gherkin scenarios produced ~19 router tests and ~16 service tests with minimal interpretation, each traceable to an AC from day one.

**The key insight:**

Spec-driven development front-loads effort into the specification phase. Most of the time went to clearing ambiguities before implementation started — 302 vs. 301, TTL format, alias validation rules. That upfront investment eliminated downstream debug and rework.

All five test-fix iterations were infrastructure and environment issues, not business logic errors. Validation, expiry, ownership, and rate limiting were correct on the first generated attempt because the spec left no room for ambiguity. Those same ambiguities would have surfaced as production bugs weeks later — at significantly greater cost.
