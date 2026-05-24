"""
Pydantic v2 request/response schemas (OpenAPI contract).

REQ: FR-001  — CreateUrlRequest / UrlResponse
REQ: FR-004  — rate limit error included in ErrorResponse
REQ: FR-007  — alias field in CreateUrlRequest
REQ: FR-008  — StatsResponse
REQ: FR-009  — expiresAt / ttl fields

All schemas match the OpenAPI contract defined in specs/url-shortener.yaml.
"""

import re
from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

# ── Alias pattern  (REQ: FR-007) ───────────────────────────────────────────
_ALIAS_RE = re.compile(r"^[A-Za-z0-9-]{3,32}$")


# ─── Request schemas ─────────────────────────────────────────────────────────

class CreateUrlRequest(BaseModel):
    """
    POST /api/v1/urls body.
    REQ: FR-001, FR-007, FR-009
    """

    long_url: Annotated[str, Field(alias="longUrl", max_length=2048)]
    alias: str | None = Field(
        default=None,
        description="Custom alias (3–32 chars, alphanumeric + hyphen). REQ: FR-007",
        pattern=r"^[A-Za-z0-9-]{3,32}$",
    )
    expires_at: datetime | None = Field(
        default=None,
        alias="expiresAt",
        description="Absolute expiry timestamp. REQ: FR-009",
    )
    ttl: str | None = Field(
        default=None,
        description="ISO 8601 duration alternative to expiresAt, e.g. P30D. REQ: FR-009",
    )

    model_config = {"populate_by_name": True}

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, v: str | None) -> str | None:
        if v is not None and not _ALIAS_RE.match(v):
            raise ValueError("alias must be 3–32 alphanumeric chars or hyphens")
        return v

    @model_validator(mode="after")
    def mutually_exclusive_expiry(self) -> "CreateUrlRequest":
        if self.expires_at and self.ttl:
            raise ValueError("Provide either expiresAt or ttl, not both")
        return self


# ─── Response schemas ─────────────────────────────────────────────────────────

class UrlResponse(BaseModel):
    """
    Returned on URL creation (201) and metadata fetch (200).
    REQ: FR-001, FR-007, FR-009
    """

    short_code: str = Field(alias="shortCode")
    short_url: str = Field(alias="shortUrl")
    long_url: str = Field(alias="longUrl")
    alias: str | None = None
    created_at: datetime = Field(alias="createdAt")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    is_active: bool = Field(alias="isActive")

    model_config = {"populate_by_name": True}


class ClickByDay(BaseModel):
    date: str
    clicks: int


class TopEntry(BaseModel):
    value: str
    clicks: int


class StatsResponse(BaseModel):
    """
    GET /api/v1/urls/{shortCode}/stats response.
    REQ: FR-008, NFR-005
    """

    short_code: str = Field(alias="shortCode")
    total_clicks: int = Field(alias="totalClicks")
    unique_visitors: int = Field(alias="uniqueVisitors")
    clicks_by_day: list[ClickByDay] = Field(default_factory=list, alias="clicksByDay")
    top_countries: list[TopEntry] = Field(default_factory=list, alias="topCountries")
    top_referrers: list[TopEntry] = Field(default_factory=list, alias="topReferrers")

    model_config = {"populate_by_name": True}


class ValidationError(BaseModel):
    field: str
    code: str
    detail: str


class ErrorResponse(BaseModel):
    """
    Unified error envelope. REQ: FR-003, FR-004, FR-006, FR-007
    Codes defined in OpenAPI spec components/schemas/ErrorResponse.
    """

    code: str = Field(
        description=(
            "Machine-readable error code: INVALID_SCHEME | SSRF_BLOCKED | "
            "ALIAS_TAKEN | RATE_LIMIT_EXCEEDED | SHORT_CODE_NOT_FOUND | "
            "URL_EXPIRED | URL_DELETED | UNAUTHORIZED | FORBIDDEN | INTERNAL_ERROR"
        )
    )
    message: str
    errors: list[ValidationError] = Field(default_factory=list)
    request_id: str | None = Field(default=None, alias="requestId")

    model_config = {"populate_by_name": True}


class HealthCheckDetail(BaseModel):
    database: str  # "ok" | "error"
    cache: str     # "ok" | "error"


class HealthResponse(BaseModel):
    """GET /health response. REQ: COMP-01 lifespan."""

    status: str       # "ok" | "degraded"
    version: str
    checks: HealthCheckDetail
