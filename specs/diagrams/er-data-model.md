# ER Diagram — URL Shortener Data Model

Full entity-relationship diagram covering all tables, fields, types,
constraints, and relationships.

---

```mermaid
erDiagram

    USERS {
        uuid        id          PK  "gen_random_uuid()"
        varchar(254) email      UK  "NOT NULL"
        varchar(64)  api_key    UK  "NOT NULL — hashed SHA-256"
        varchar(20)  plan           "free | pro | enterprise"
        int          rate_limit     "requests per minute override, NULL = plan default"
        boolean      is_active      "DEFAULT true"
        timestamptz  created_at     "DEFAULT now()"
        timestamptz  updated_at     "DEFAULT now()"
    }

    URLS {
        uuid        id          PK  "gen_random_uuid()"
        uuid        user_id     FK  "NOT NULL → USERS.id"
        text        long_url        "NOT NULL, max 2048 chars"
        varchar(32) short_code  UK  "NOT NULL — NanoID or custom alias"
        varchar(32) custom_alias    "NULL if auto-generated"
        boolean     is_active       "DEFAULT true"
        timestamptz expires_at      "NULL = no expiry"
        timestamptz created_at      "DEFAULT now()"
        timestamptz updated_at      "DEFAULT now()"
        timestamptz deleted_at      "NULL = not deleted (soft delete)"
    }

    CLICKS {
        uuid        id          PK  "gen_random_uuid()"
        uuid        url_id      FK  "NOT NULL → URLS.id"
        varchar(64) ip_hash         "NOT NULL — SHA-256(ip + tenant_salt)"
        varchar(512) user_agent     "NULL acceptable"
        varchar(512) referer        "NULL acceptable"
        char(2)     country_code    "ISO 3166-1 alpha-2, NULL if unknown"
        timestamptz clicked_at      "NOT NULL DEFAULT now()"
    }

    RATE_LIMIT_WINDOWS {
        varchar(128) bucket_key  PK  "format: <userId>:<windowStart>"
        int          count           "NOT NULL DEFAULT 0"
        int          limit           "NOT NULL — copied from user plan at window start"
        timestamptz  window_start    "NOT NULL"
        timestamptz  reset_at        "NOT NULL — window_start + 60 s"
    }

    AUDIT_LOG {
        uuid        id          PK  "gen_random_uuid()"
        uuid        user_id     FK  "NULL for unauthenticated events"
        varchar(64) event_type      "NOT NULL — e.g. SSRF_BLOCKED, URL_CREATED"
        varchar(32) short_code      "NULL for non-URL events"
        jsonb       metadata        "arbitrary event payload"
        varchar(45) client_ip       "raw IP retained for security log only (separate retention policy)"
        timestamptz occurred_at     "NOT NULL DEFAULT now()"
    }

    USERS     ||--o{ URLS               : "owns (user_id)"
    URLS      ||--o{ CLICKS             : "receives clicks (url_id)"
    USERS     ||--o{ RATE_LIMIT_WINDOWS : "throttled by (userId in bucket_key)"
    USERS     ||--o{ AUDIT_LOG          : "actor in (user_id)"
```

---

## Index Strategy

| Table    | Index Name                         | Columns                        | Type        | Purpose                                     |
|----------|------------------------------------|--------------------------------|-------------|---------------------------------------------|
| `urls`   | `idx_urls_short_code`              | `short_code`                   | B-tree UK   | O(1) redirect lookup (hot path)             |
| `urls`   | `idx_urls_user_id_created`         | `user_id, created_at DESC`     | B-tree      | List user's URLs sorted by recency          |
| `urls`   | `idx_urls_expires_at_active`       | `expires_at, is_active`        | B-tree      | TTL expiry sweeper job                      |
| `clicks` | `idx_clicks_url_id_clicked_at`     | `url_id, clicked_at DESC`      | B-tree      | Time-series stats queries per link          |
| `clicks` | `idx_clicks_clicked_at`            | `clicked_at`                   | B-tree      | Global analytics rollups (partition pruning)|
| `audit`  | `idx_audit_event_occurred`         | `event_type, occurred_at DESC` | B-tree      | Security monitoring queries                 |

## Partitioning Note

`CLICKS` SHOULD be range-partitioned by `clicked_at` (monthly) to keep
query performance predictable as the table grows into hundreds of millions
of rows. Partition pruning ensures stats queries scan only the relevant months.

## Migration Strategy

This is a greenfield schema — no existing data migration required for v1.
Future migrations MUST use sequential, non-locking operations:
`CREATE INDEX CONCURRENTLY`, `ADD COLUMN` with a default before `SET NOT NULL`.
