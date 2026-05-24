"""
SQLAlchemy ORM models.

REQ: FR-001  — urls table stores long_url and short_code.
REQ: FR-005  — soft delete via deleted_at (ADR-02).
REQ: FR-006  — expires_at enables 410 Gone responses.
REQ: FR-008  — clicks table records redirect events.
REQ: NFR-005 — ip_hash only (no raw IPs) in clicks table.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ─── Users ────────────────────────────────────────────────────────────────────
class User(Base):
    """API consumers identified by a hashed API key."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    # REQ: NFR-004 — API key stored as SHA-256 hash, never plaintext
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(20), default="free")
    rate_limit_override: Mapped[int | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    urls: Mapped[list["URL"]] = relationship("URL", back_populates="user")


# ─── URLs ─────────────────────────────────────────────────────────────────────
class URL(Base):
    """
    Core entity: a mapping from short_code to long_url.

    REQ: FR-001  — short_code is unique, auto-generated or custom alias.
    REQ: FR-005  — deleted_at enables soft delete (ADR-02).
    REQ: FR-006  — expires_at enables TTL / 410 Gone.
    REQ: FR-007  — custom_alias column holds user-supplied alias.
    """

    __tablename__ = "urls"
    __table_args__ = (
        CheckConstraint("length(long_url) <= 2048", name="ck_urls_long_url_length"),
        # REQ: FR-001 — short_code unique index (hot-path lookup)
        Index("idx_urls_short_code", "short_code", unique=True),
        Index("idx_urls_user_id_created", "user_id", "created_at"),
        # REQ: FR-006 — partial index for TTL sweeper
        Index(
            "idx_urls_expires_active",
            "expires_at",
            "is_active",
            postgresql_where="is_active = true AND expires_at IS NOT NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    long_url: Mapped[str] = mapped_column(Text, nullable=False)
    short_code: Mapped[str] = mapped_column(String(32), nullable=False)
    custom_alias: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # REQ: FR-005, ADR-02 — soft delete
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship("User", back_populates="urls")
    clicks: Mapped[list["Click"]] = relationship("Click", back_populates="url")

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        from datetime import timezone
        now = datetime.now(timezone.utc)
        # SQLite returns tz-naive datetimes; normalise before comparison.
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return now > exp


# ─── Clicks ──────────────────────────────────────────────────────────────────
class Click(Base):
    """
    Immutable click event recorded on each redirect.

    REQ: FR-008  — analytics per short link.
    REQ: NFR-005 — ip_hash only; no raw IP persisted.
    """

    __tablename__ = "clicks"
    __table_args__ = (
        Index("idx_clicks_url_id_ts", "url_id", "clicked_at"),
        Index("idx_clicks_clicked_at", "clicked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    url_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("urls.id"), nullable=False
    )
    # REQ: NFR-005 — SHA-256(ip + per-deployment-salt), never raw IP
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    referer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    clicked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    url: Mapped["URL"] = relationship("URL", back_populates="clicks")


# ─── Audit Log ───────────────────────────────────────────────────────────────
class AuditLog(Base):
    """
    Security-relevant events (SSRF attempts, deletions, suspensions).

    REQ: NFR-003 — SSRF_BLOCKED events logged here.
    REQ: NFR-005 — client_ip retained under security log retention policy only.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_event_ts", "event_type", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    short_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Separate security-log retention — NOT the same as analytics
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
