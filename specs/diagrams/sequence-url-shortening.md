# Sequence Diagram — URL Shortening & Redirect Flow

Two primary flows are shown: **URL creation** (authenticated) and **redirect** (public).

---

## Flow 1: Create Short URL

```mermaid
sequenceDiagram
    autonumber
    actor Client as API Client
    participant GW   as API Gateway<br/>(rate limiter + auth)
    participant SVC  as URL Service
    participant Val  as URL Validator<br/>(SSRF + scheme check)
    participant Cache as Redis Cache
    participant DB   as PostgreSQL
    participant Evt  as Event Bus<br/>(async analytics)

    Client->>GW: POST /api/v1/urls<br/>{ longUrl, alias?, expiresAt? }<br/>X-API-Key: <key>

    GW->>GW: Validate API key → resolve userId
    GW->>GW: Check rate limit bucket (100 req/min per key)

    alt Rate limit exceeded
        GW-->>Client: 429 Too Many Requests<br/>Retry-After: <seconds>
    end

    GW->>SVC: createUrl(userId, longUrl, alias, expiresAt)

    SVC->>Val: validate(longUrl)
    Val->>Val: Check scheme (http/https only)
    Val->>Val: Resolve hostname — block RFC 1918 / link-local

    alt Invalid scheme or SSRF target
        Val-->>SVC: ValidationError(code)
        SVC-->>GW: 422 Unprocessable Entity
        GW-->>Client: { code: INVALID_SCHEME | SSRF_BLOCKED }
    end

    Val-->>SVC: OK

    opt alias provided
        SVC->>DB: SELECT id FROM urls WHERE short_code = alias
        DB-->>SVC: row | null

        alt Alias already taken
            SVC-->>GW: 409 Conflict
            GW-->>Client: { code: ALIAS_TAKEN }
        end
    end

    SVC->>SVC: Generate shortCode (NanoID 8 chars) if no alias

    SVC->>DB: INSERT INTO urls (short_code, long_url, user_id,<br/>       expires_at, created_at)
    DB-->>SVC: url record

    SVC->>Cache: SET shortCode → longUrl<br/>EX = TTL (or 24 h default)
    Cache-->>SVC: OK

    SVC-->>GW: UrlResponse
    GW-->>Client: 201 Created<br/>{ shortUrl, shortCode, expiresAt, createdAt }
```

---

## Flow 2: Redirect (Public Hot Path)

```mermaid
sequenceDiagram
    autonumber
    actor Visitor as Browser / Client
    participant Edge  as CDN / Edge Layer
    participant SVC   as URL Service
    participant Cache as Redis Cache
    participant DB    as PostgreSQL
    participant Evt   as Analytics Worker<br/>(async)

    Visitor->>Edge: GET /aB3xY9

    Edge->>Cache: GET aB3xY9
    Cache-->>Edge: longUrl (cache hit) OR nil

    alt Cache HIT (≥ 95% of requests)
        Edge-->>Visitor: 302 Found<br/>Location: <longUrl><br/>Cache-Control: no-store
        Edge-)Evt: publish ClickEvent{ shortCode, ip, ua, referer, ts } [fire-and-forget]
    else Cache MISS
        Edge->>SVC: resolve(aB3xY9)
        SVC->>DB: SELECT long_url, expires_at, deleted_at<br/>FROM urls WHERE short_code = 'aB3xY9'
        DB-->>SVC: row | null

        alt Not found
            SVC-->>Edge: 404 Not Found
            Edge-->>Visitor: 404 { code: SHORT_CODE_NOT_FOUND }
        else Expired
            SVC-->>Edge: 410 Gone
            Edge-->>Visitor: 410 { code: URL_EXPIRED }
        else Deleted
            SVC-->>Edge: 404 Not Found
            Edge-->>Visitor: 404 { code: SHORT_CODE_NOT_FOUND }
        else Active
            SVC->>Cache: SET aB3xY9 → longUrl EX 86400
            SVC-->>Edge: longUrl
            Edge-->>Visitor: 302 Found<br/>Location: <longUrl>
            Edge-)Evt: publish ClickEvent [fire-and-forget]
        end
    end

    Note over Evt: Async: hash IP (SHA-256 + salt),<br/>geolocate country, persist CLICKS row
```

---

## Flow 3: Delete Short URL

```mermaid
sequenceDiagram
    autonumber
    actor Owner as URL Owner
    participant GW  as API Gateway
    participant SVC as URL Service
    participant Cache as Redis Cache
    participant DB  as PostgreSQL

    Owner->>GW: DELETE /api/v1/urls/aB3xY9<br/>X-API-Key: <key>
    GW->>GW: Validate API key → resolve userId
    GW->>SVC: deleteUrl(userId, "aB3xY9")
    SVC->>DB: SELECT user_id FROM urls WHERE short_code = 'aB3xY9'
    DB-->>SVC: row

    alt Not owner
        SVC-->>GW: 403 Forbidden
        GW-->>Owner: { code: FORBIDDEN }
    else Owner confirmed
        SVC->>DB: UPDATE urls SET deleted_at = NOW()<br/>WHERE short_code = 'aB3xY9'
        DB-->>SVC: OK
        SVC->>Cache: DEL aB3xY9
        Cache-->>SVC: OK
        SVC-->>GW: 204 No Content
        GW-->>Owner: 204 No Content
    end
```
