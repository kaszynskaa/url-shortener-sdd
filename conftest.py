"""
Root pytest configuration — runs before any test module is imported.

Sets environment variables so pydantic-settings picks up SQLite/test values
before src.database.engine is created.
"""
import os

# ── Override before src.* imports ─────────────────────────────────────────────
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BASE_URL", "https://sho.rt")
os.environ.setdefault("IP_HASH_SALT", "test-salt-do-not-use-in-production")
os.environ.setdefault("DEBUG", "false")
