# Configuration

- [Precedence](#precedence)
- [Environment variables](#environment-variables)
- [`limits`](#limits)
- [`rate_limit`](#rate_limit)
- [`replay`](#replay)
- [`retention`](#retention)
- [`search`](#search)
- [Backup and restore](#backup-and-restore)
- [Per-endpoint overrides](#per-endpoint-overrides)

---

## Precedence

Highest wins:

```
environment  >  .env  >  config.yaml  >  defaults
```

Secrets belong in `.env`, which is gitignored. `config.yaml` is for non-secret tuning and is
gitignored too; commit neither. Start from the examples:

```bash
cp .env.example .env
cp config.example.yaml config.yaml     # optional
```

Point `CONFIG_FILE` at another path to use a different file.

**Nested keys use a double underscore in the environment.** `rate_limit.requests_per_minute` becomes
`RATE_LIMIT__REQUESTS_PER_MINUTE`. This is how you tune a value in Docker Compose without editing
`config.yaml`:

```yaml
services:
  app:
    environment:
      RATE_LIMIT__REQUESTS_PER_MINUTE: "240"
```

---

## Environment variables

Top-level settings, normally set in `.env`.

| Variable | Default | Notes |
|---|---|---|
| `APP_PORT` | `8000` | Host port published by Compose |
| `APP_HOST` | `0.0.0.0` | Bind address inside the container |
| `MONGO_URI` | `mongodb://mongodb:27017` | Includes credentials under the production overlay |
| `MONGO_DATABASE` | `webhook_inbox` | |
| `ADMIN_USERNAME` | `admin` | |
| `ADMIN_PASSWORD` | *(empty)* | Empty means one is generated and printed once at first startup |
| `LOG_LEVEL` | `INFO` | |

**`ADMIN_PASSWORD` is only applied while no user exists.** It seeds the first account and is ignored
afterwards, so changing it later does nothing — change the password in Settings instead. If you left
it empty, the generated password is printed to the container log exactly once:

```
ADMIN_PASSWORD was not set. Generated a password for 'admin':

    k7Qw2mR9xLpV

Change it in Settings, or set ADMIN_PASSWORD and start with an empty database.
```

That line deliberately bypasses log redaction. A redacted bootstrap credential would be useless.

### Production overlay only

Used by [`compose.prod.yaml`](../compose.prod.yaml), and **only applied when the Mongo volume is
first created**:

| Variable | Notes |
|---|---|
| `MONGO_ROOT_USERNAME` / `MONGO_ROOT_PASSWORD` | MongoDB root account |
| `MONGO_APP_USERNAME` / `MONGO_APP_PASSWORD` | The account the app connects with, granted `readWrite` on its own database and nothing else |

Use URL-safe passwords: they are embedded in the connection string.

---

## `limits`

```yaml
limits:
  max_payload_size: 10485760   # 10 MiB
  request_timeout: 10
  max_header_count: 100
  max_header_bytes: 16384
  max_query_length: 4096
  max_json_depth: 100
```

| Key | Default | Effect |
|---|---|---|
| `max_payload_size` | 10 MiB | Bodies above this are refused with 413, before being buffered |
| `request_timeout` | 10 | Seconds |
| `max_header_count` | 100 | More headers than this is refused with 431 |
| `max_header_bytes` | 16384 | Total header size; 431 when exceeded |
| `max_query_length` | 4096 | Longer query strings are refused with 414 |
| `max_json_depth` | 100 | Deeper JSON is stored raw but not parsed |

Header, query and depth limits apply to **webhook ingestion**, not dashboard requests. The first
three are checked before any database access, so cheap garbage cannot cost a query.

`max_json_depth` exists because parsing, key escaping and tokenising all recurse. A body nested a few
thousand deep is only a few kilobytes, so it passes the size check; without this limit it would
exhaust the stack. Over-deep bodies are accepted and stored with `raw_body` intact and `body: null`.

---

## `rate_limit`

```yaml
rate_limit:
  enabled: true
  requests_per_minute: 120
  login_per_minute: 10
  trust_forwarded_for: false
```

| Key | Default | Effect |
|---|---|---|
| `enabled` | `true` | |
| `requests_per_minute` | 120 | Webhook ingestion, per endpoint **and** source address |
| `login_per_minute` | 10 | Sign-in, per source address |
| `trust_forwarded_for` | `false` | Whether to read the client address from `X-Forwarded-For` |

Ingestion and sign-in have **separate budgets**, so a webhook flood cannot lock you out of the
dashboard and a password-guessing attack cannot stop events arriving.

Throttling fires **before** credentials are checked, so a correct password inside a throttled window
still returns 429. Otherwise the status code would tell an attacker they had guessed right.

**Only enable `trust_forwarded_for` behind a proxy you control.** Unproxied, anyone can forge the
header and get a fresh budget per request, which disables the limit entirely. When enabled, the
**last** entry is used — the one your proxy appended; earlier entries are client-supplied.

Limiter state is per process and held in memory. Running two app processes means each allows the full
rate.

---

## `replay`

```yaml
replay:
  enabled: true
  timeout: 10
  max_retries: 3
  retry_delay_seconds: 2
  allow_private_networks: false
  allow_redirects: false
  max_redirects: 3
  max_response_size: 65536
  worker_enabled: true
  poll_interval: 1.0
  lease_timeout: 60
```

| Key | Default | Effect |
|---|---|---|
| `enabled` | `true` | Turns the feature off entirely, API and UI |
| `timeout` | 10 | Seconds per attempt |
| `max_retries` | 3 | Retries *after* the first attempt, so four attempts in total |
| `retry_delay_seconds` | 2 | Base delay; backoff doubles it each attempt |
| `allow_private_networks` | `false` | See the warning below |
| `allow_redirects` | `false` | When enabled, every hop is re-validated |
| `max_redirects` | 3 | Hops beyond the first |
| `max_response_size` | 64 KiB | Response bodies are truncated past this |
| `worker_enabled` | `true` | Set `false` when replay runs as its own service |
| `poll_interval` | 1.0 | Seconds between queue polls when idle |
| `lease_timeout` | 60 | Seconds before a stalled job is reclaimed |

> **`allow_private_networks` is all-or-nothing.** Enabling it opens every private range at once,
> including cloud metadata endpoints such as `169.254.169.254`. Use it for local development only.

Retries apply to timeouts, connection errors and 5xx responses. A 4xx is never retried, and neither
is a destination rejected by validation.

---

## `retention`

```yaml
retention:
  enabled: true
  default_days: 30
```

| Key | Default | Effect |
|---|---|---|
| `enabled` | `true` | When `false`, events are kept until deleted by hand |
| `default_days` | 30 | Applied to endpoints with no override |

**Deletion is approximate.** MongoDB's TTL monitor runs about once a minute, so events are removed
shortly after expiry, not exactly on it.

Changing an endpoint's retention recomputes the expiry of its stored events. Changing
`default_days` affects only events received afterwards.

---

## `search`

```yaml
search:
  enabled: true
  fuzzy: true
```

Search terms are derived at ingest, so changing tokenisation requires a rebuild of existing events:

```bash
docker compose exec app python -m app.search.backfill --rebuild
```

Without `--rebuild` the command only fills in events that have no search terms at all, which is what
you want after upgrading from a version that predates the field.

---

## Backup and restore

Events live in the `mongodb_data` Docker volume, which is independent of the containers. Rebuilding
or recreating the application does not touch it; `docker compose down -v` destroys it.

**Back up:**

```bash
docker compose exec -T mongodb mongodump \
  --db webhook_inbox --archive --gzip > backup-$(date +%F).archive.gz
```

**Restore:**

```bash
docker compose exec -T mongodb mongorestore \
  --archive --gzip --drop < backup-2026-08-11.archive.gz
```

`--drop` replaces the existing collections. Without it, documents are merged and duplicate `_id`s are
skipped, which quietly leaves you with a mixture of both datasets.

Under the production overlay MongoDB requires authentication, so add credentials:

```bash
docker compose -f compose.yaml -f compose.prod.yaml exec -T mongodb \
  mongodump --username "$MONGO_ROOT_USERNAME" --password "$MONGO_ROOT_PASSWORD" \
  --authenticationDatabase admin --db webhook_inbox --archive --gzip > backup.archive.gz
```

Two things worth knowing:

- **Indexes are included in the dump and rebuilt on restore.** `ensure_indexes()` also runs at
  startup and is idempotent, so a restore into a running deployment converges either way.
- **Retention keeps running after a restore.** Events restored past their `expires_at` are removed by
  the TTL monitor within roughly a minute. To keep an old archive intact, restore it into a
  deployment with `retention.enabled: false`, or clear the field:
  ```bash
  docker compose exec -T mongodb mongosh --quiet --eval \
    'db.getSiblingDB("webhook_inbox").events.updateMany({}, {$unset: {expires_at: ""}})'
  ```

---

## Per-endpoint overrides

Set per endpoint in the dashboard or through `PATCH /api/endpoints/{id}`:

| Field | Effect |
|---|---|
| `enabled` | Disabled endpoints reject with 403 |
| `authentication.type` | `none`, `static_secret` or `hmac_sha256` |
| `authentication.header` | Defaults to `x-webhook-secret` or `x-hub-signature-256` |
| `authentication.signature_prefix` | Stripped before comparison, e.g. `sha256=` |
| `secret` | Write-only; the API returns `has_secret`, never the value |
| `allowed_methods` | Others are refused with 405 |
| `max_payload_size` | Overrides the global limit |
| `retention_days` | Overrides the global default |
