# URL Shortener — Spec-Driven AI Development

A production-quality URL shortener built entirely through **spec-driven development with Claude**. Every file in this repository was generated from a formal specification — prompts, plans, implementation, security review, and tests all trace back to a single source-of-truth YAML spec.

**47/47 tests passing** · OWASP reviewed · Full traceability matrix

---

## What this demonstrates

| Practice | Artefact |
| -------- | -------- |
| YAML prompt library | `prompts/` — 4 reusable, version-controlled prompt templates |
| Formal specification | `specs/url-shortener.yaml` — FR/NFR/Gherkin/OpenAPI in one file |
| Visual modelling | `specs/diagrams/` — sequence, ER, and state diagrams (HTML/Mermaid) |
| Architecture plan | `plans/implementation-plan.yaml` — component map, ADRs, risk register |
| Traceable implementation | `src/` — every function carries `# REQ: FR-NNN` comments |
| Self-critique loop | `reviews/` — OWASP findings with before/after diffs |
| JSON Schema enforcement | `schema_runner/` — Anthropic `tool_use` API + `jsonschema.validate()` |
| Auto-generated tests | `tests/` — generated from `test-generator.yaml`, covers all 10 Gherkin scenarios |
| Traceability matrix | `docs/traceability-matrix.md` — requirement → code → test → status |
| Written reflection | `REPORT.md` — 12 questions on spec-driven development |

---

## Repository structure

```
.
├── prompts/                    # Reusable YAML prompt templates
│   ├── spec-writer.yaml        # Product analyst — generates FR/NFR/Gherkin
│   ├── architect.yaml          # Senior architect — generates implementation plan
│   ├── code-reviewer.yaml      # Security reviewer — OWASP Top 10 analysis
│   └── test-generator.yaml     # QA engineer — generates runnable test code
│
├── specs/
│   ├── url-shortener.yaml      # SPEC-20260524-001 — master specification
│   └── diagrams/
│       ├── sequence-url-shortening.html   # Create / redirect / delete flows
│       ├── er-data-model.html             # 5-table schema + index strategy
│       └── state-url-lifecycle.html       # URL state machine (active→deleted→suspended)
│
├── plans/
│   └── implementation-plan.yaml   # ARCH-20260524-001 — 5 build phases, 4 ADRs
│
├── reviews/
│   ├── initial-code-review.json   # SEC-20260524-001 — 5 OWASP findings
│   └── post-fix-review.json       # SEC-20260524-002 — post-fix verification
│
├── schema_runner/
│   ├── schema_runner.py        # Anthropic tool_use + jsonschema enforcement
│   ├── output_schema.json      # JSON Schema 2020-12 for UrlResponse
│   └── validated_output.json   # Verified Claude output
│
├── src/
│   ├── main.py                 # FastAPI app with lifespan context manager
│   ├── config.py               # pydantic-settings — env/config management
│   ├── database.py             # SQLAlchemy async engine (_build_engine factory)
│   ├── models.py               # ORM: User, URL, Click, AuditLog
│   ├── schemas.py              # Pydantic v2 request/response models
│   ├── cache.py                # Redis cache_get / cache_set / cache_incr helpers
│   ├── middleware/
│   │   ├── auth.py             # API key auth (SHA-256 hash lookup)
│   │   └── rate_limiter.py     # 100 req/min per-user sliding window (Redis)
│   ├── routers/
│   │   ├── urls.py             # POST /api/v1/urls, DELETE /api/v1/urls/{code}
│   │   ├── redirect.py         # GET /{short_code} — cache-first 302 redirect
│   │   ├── stats.py            # GET /api/v1/urls/{code}/stats
│   │   └── health.py           # GET /health — DB + Redis liveness
│   ├── services/
│   │   ├── url_service.py      # create_url, resolve_url, delete_url
│   │   ├── validator.py        # SSRF blocking (14-entry blocklist, IPv4 + IPv6)
│   │   └── analytics_service.py
│   └── utils/
│       ├── codegen.py          # 8-char NanoID via secrets.choice (CSPRNG)
│       └── crypto.py           # SHA-256 IP hashing, API key hashing
│
├── tests/
│   ├── conftest.py             # SQLite in-memory fixtures, Redis mocks
│   ├── test_validator.py       # 12 tests — scheme + SSRF validation
│   ├── test_url_service.py     # 16 tests — create, resolve, delete business logic
│   └── test_routers.py         # 19 tests — all 10 Gherkin scenarios via HTTP
│
├── docs/
│   ├── traceability-matrix.md  # FR/NFR/Gherkin/AC → code → test → status
│   └── self-critique-log.md    # Generate → Review → Fix cycle documentation
│
├── REPORT.md                   # Written answers to 12 SDD reflection questions
├── conftest.py                 # Root conftest — sets env vars before src.* imports
├── pyproject.toml              # pytest-asyncio config (asyncio_mode = "auto")
└── requirements.txt            # All dependencies
```

---

## Security findings fixed

The self-critique loop (`code-reviewer.yaml` → OWASP Top 10) caught four blocking issues before any tests ran:

| ID | Severity | Category | Issue | Fix |
| -- | -------- | -------- | ----- | --- |
| FIND-001 | High | A10 SSRF | Blocklist missing `169.254.0.0/16` (AWS metadata), CGNAT, IPv6 ranges | Expanded to 14-entry blocklist |
| FIND-002 | High | A01 IDOR | `delete_url()` had no ownership check — any user could delete any URL | Added `requesting_user_id` parameter + ownership guard |
| FIND-003 | Medium | A04 Design | ISO 8601 `ttl` field (`P30D`) silently ignored | Added `_parse_ttl()` parser in `url_service.py` |
| FIND-004 | Medium | A04 Design | Rate-limit bucket was global, not per-user | Bucket key scoped to `rl:{user_id}:{window}` |

Full diffs in `docs/self-critique-log.md` and `reviews/`.

---

## Running the tests

```bash
# Install dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the full test suite (SQLite in-memory, Redis mocked)
pytest tests/ -v
# → 47 passed in 0.19s
```

No PostgreSQL or Redis instance required — the test suite uses SQLite `StaticPool` and `unittest.mock` patches at every import site.

---

## Running the schema enforcement demo

```bash
export ANTHROPIC_API_KEY=sk-...
python schema_runner/schema_runner.py
# → Calls Claude via tool_use, validates output against JSON Schema 2020-12
# → Writes validated_output.json
```

---

## Spec → code traceability

Every source function is annotated with the requirement it implements:

```python
# src/services/validator.py
# REQ: NFR-003 — block SSRF targeting RFC 1918 + link-local + IPv6 private ranges

# src/services/url_service.py
# REQ: FR-001 — accept long URL, return unique short code
# REQ: FR-009 — parse ISO 8601 ttl string as alternative to expiresAt
```

The full mapping lives in `docs/traceability-matrix.md` — 83% of requirements have full test coverage; the remaining 17% are deferred to Phase 4 (deduplication, analytics stats, load tests).

---

## Tech stack

| Layer | Technology |
| ----- | ---------- |
| API framework | FastAPI 0.115 |
| ORM | SQLAlchemy 2.0 async (`AsyncSession`) |
| Production DB | PostgreSQL + asyncpg |
| Test DB | SQLite + aiosqlite (`StaticPool`) |
| Cache | Redis (hiredis) — cache-aside pattern |
| Validation | Pydantic v2 |
| Auth | SHA-256 hashed API keys (`X-API-Key` header) |
| AI SDK | Anthropic Python SDK (`tool_use` + JSON Schema) |
| Testing | pytest-asyncio, ASGI transport, unittest.mock |
