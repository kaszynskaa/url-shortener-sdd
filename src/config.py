"""
Application configuration.

REQ: NFR-004 — All write endpoints require authentication (API key).
REQ: FR-004  — Rate limit of 100 req/min per API key.
REQ: NFR-006 — Redis cache for redirect hot path.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ────────────────────────────────────────────────────────────────
    app_name: str = "URL Shortener API"
    app_version: str = "1.0.0"
    base_url: str = "https://sho.rt"          # Used to build shortUrl responses
    debug: bool = False

    # ── Database  (REQ: COMP-02) ───────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/urlshortener"

    # ── Cache  (REQ: NFR-006) ──────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 86_400           # 24 h default TTL for cached codes

    # ── Short code generator  (REQ: FR-001, ADR-04) ────────────────────────
    short_code_length: int = 8
    short_code_max_retries: int = 3

    # ── Rate limiting  (REQ: FR-004) ───────────────────────────────────────
    rate_limit_max_requests: int = 100
    rate_limit_window_seconds: int = 60

    # ── Security  (REQ: NFR-003, NFR-004, NFR-005) ─────────────────────────
    api_key_header: str = "X-API-Key"
    ip_hash_salt: str = "change-me-in-production-env"   # per-deployment secret

    # ── Analytics cleanup  (REQ: FR-006, ADR-02) ──────────────────────────
    soft_delete_retention_days: int = 90
    short_code_reuse_freeze_days: int = 30

    @field_validator("base_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


settings = Settings()
