# Security

What this service defends against, how, and what it deliberately does not defend against.

To report a vulnerability, see [SECURITY.md](../SECURITY.md).

- [Threat model](#threat-model)
- [Ingestion](#ingestion)
- [Replay and SSRF](#replay-and-ssrf)
- [Authentication](#authentication)
- [Secrets](#secrets)
- [Response hardening](#response-hardening)
- [Known limitations](#known-limitations)
- [Production checklist](#production-checklist)

---

## Threat model

Two facts drive everything here:

1. **`/webhooks/{path}` accepts anonymous traffic from the internet.** Anything reaching it is
   hostile input until proven otherwise, and it must stay cheap to reject.
2. **Replay sends stored data to a URL the user supplies.** Without constraints that is a
   server-side request forgery engine pointed at whatever the host can reach — including cloud
   metadata endpoints and the database itself.

Everything below follows from those two.

Out of scope: the dashboard has no roles. Every authenticated user has full access, including reading
every payload and deleting endpoints. If you need per-user restrictions, this is not the tool.

---

## Ingestion

**Checks run cheapest-first.** Header count, header size and query length are validated, then the
rate limit, and only then is the database touched. A flood of malformed requests cannot become a
flood of queries.

**Oversized bodies are refused before buffering.** `Content-Length` is checked when present, and a
streamed body is abandoned the moment it exceeds the limit — a chunked request that lies about its
length is still caught.

**Nesting depth is bounded.** Parsing, key escaping and search tokenisation all recurse. A body
nested a few thousand deep is only a few kilobytes, so it passes the size check and would exhaust the
stack. Depth is measured over the raw bytes first, correctly ignoring brackets inside strings.
Over-deep bodies are stored with the raw bytes intact and simply not walked.

**Signature verification fails closed.** An unrecognised authentication type, a missing secret or a
missing header are rejections, never passes. Verification runs against the untouched bytes before any
parsing, because re-serialising JSON changes whitespace and key order and would invalidate every
HMAC. Comparison uses `hmac.compare_digest`.

**Payloads are escaped, not sanitised.** MongoDB-restricted key names are stored with lookalike
codepoints and unescaped on read. Output escaping happens at render time — the JSON viewer escapes
every key, value and summary individually before marking the assembled HTML safe.

---

## Replay and SSRF

**Destinations are validated twice.** Once by the API when the replay is queued, so the user gets
immediate feedback and nothing invalid is ever stored; once by the worker immediately before
connecting. The gap between those two moments is exactly where DNS can change underneath you.

Validation rejects:

- any scheme other than `http` and `https` — so `file://`, `gopher://`, `ftp://` and the rest cannot
  be reached;
- URLs carrying credentials, a common obfuscation trick that would also end up in logs;
- private, loopback, link-local, multicast, reserved and unspecified addresses, **IPv4 and IPv6**,
  including IPv4-mapped IPv6 such as `::ffff:127.0.0.1`.

**A hostname resolving to several addresses is refused if any one of them is blocked.** One public
answer does not excuse a private one.

**Connections go to the validated IP**, with `Host` and TLS SNI carrying the real hostname. Resolving
for validation and then letting the HTTP client resolve again at connect time is the classic DNS
rebinding hole; pinning closes it.

**Redirects are followed by hand**, with the client always set to `follow_redirects=False`. Each hop
is re-validated from scratch. Letting the client follow redirects would re-resolve DNS entirely
outside validation.

**Credentials are never forwarded.** `Authorization`, `Cookie` and `Proxy-Authorization` are stripped
from replayed requests. Signature headers such as `x-hub-signature-256` *are* forwarded: they are
derived from the body being sent, are useless for anything else, and replaying them is the entire
point of testing a receiver's verification locally.

Responses are capped at 64 KiB. Validation failures are never retried.

---

## Authentication

**Default deny.** Everything except `/health`, `/ready`, `/login`, `/api/auth/login`, `/webhooks/`
and `/static/` requires a session, so a new route is protected unless it is explicitly opened.

**Passwords** use Argon2id. **Session tokens** are random 32-byte values stored only as SHA-256
hashes — a database dump cannot be replayed as a session.

**Sign-in timing is equalised.** An unknown username is verified against a dummy hash so it takes the
same time as a wrong password, and cannot be distinguished.

**Sign-in is throttled**, on a separate budget from ingestion, and the throttle fires *before*
credentials are checked. A correct password inside a throttled window still returns 429; otherwise
the status code would confirm the guess.

**Changing a password revokes every session** for that user, including the current one.

**CSRF** uses a per-session synchroniser token sent as a header, compared with `hmac.compare_digest`.
Header-only was a deliberate choice: reading a form body in middleware consumes the request stream
before the route handler can read it. The trade-off is that mutations require JavaScript, which is
acceptable for a developer dashboard.

Session cookies are `HttpOnly` and `SameSite=Lax`, and gain `Secure` when the request is HTTPS or
arrives with `X-Forwarded-Proto: https`. That header is honoured without configuration because
forging it can only *add* `Secure`, never remove it.

---

## Secrets

**Endpoint signing secrets are write-only.** The API and dashboard report `has_secret`, never the
value. Submitting an empty secret field means "keep the stored one", not "clear it".

**Logs are redacted** by a structlog processor covering authorization, cookie, password, secret,
token, api key and signature headers, matched as substrings so `X-Hub-Signature-256` and
`user_password` are both caught, at the top level and one level down.

**Search excludes sensitive keys at write time**, reusing the same check. A payload field called
`api_key` is stored and visible on the event page but never becomes a searchable term — otherwise
search would reintroduce the leak redaction exists to prevent.

**One deliberate exception:** the generated admin password is printed once to stdout at first
startup, bypassing redaction. A redacted bootstrap credential would be useless to the operator.

---

## Response hardening

Every response, including errors and redirects, carries:

| Header | Value |
|---|---|
| `Content-Security-Policy` | `default-src 'self'`, `frame-ancestors 'none'`, `base-uri 'none'` |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `Strict-Transport-Security` | Only when the request is actually HTTPS |

**The CSP still requires `'unsafe-inline'`** for scripts and styles, because the theme-toggle script
and several `onclick` handlers are inline. That materially weakens its value against XSS. What it
does buy is blocking remote script loading, `connect-src` exfiltration and framing. Removing the
inline handlers is outstanding work.

---

## Known limitations

Accepted trade-offs, not oversights.

- **The seeded `demo` endpoint accepts unauthenticated writes.** It exists so `docker compose up`
  is immediately usable. Delete or disable it on an internet-facing deployment.
- **Rate limiting is per process and in memory.** Two app processes each allow the full rate.
  A shared counter would mean adding Redis for a single counter.
- **A distributed flood still reaches the endpoint lookup.** Each distinct source gets its own
  budget. This is not DDoS protection.
- **`allow_private_networks` is all-or-nothing**, including cloud metadata endpoints when enabled.
- **Replay response bodies are stored as returned.** A destination that echoes a secret back has it
  written to the database, capped but not redacted.
- **Exported payloads are unredacted.** Redaction covers logs only.
- **Export is unbounded.** Memory stays flat because it streams, but a filterless export of a very
  large collection holds a cursor open for a long time.
- **Dashboard query parameters are not length-capped.** Measured: a 20,000-character search returns
  in 9 ms, 4,000 distinct terms in 239 ms, because tokens are truncated and trigram arrays capped.
  Authenticated-only and not a practical denial of service, but it is unbounded input.
- **`/health` and `/ready` are unauthenticated and unthrottled**, and `/ready` pings MongoDB.
- **Fuzzy search cannot survive transposition.** `jhon` does not find `john`.

---

## Production checklist

1. Use the production overlay — MongoDB then requires authentication and the app connects as a user
   holding `readWrite` on its own database only:
   ```bash
   docker compose -f compose.yaml -f compose.prod.yaml up -d
   ```
2. Start from an **empty** Mongo volume, or the credentials are never applied and you get an
   authenticated server with no users.
3. Set `ADMIN_PASSWORD`, or capture the generated one printed at first startup, then change it.
4. Terminate TLS at a proxy that sets `X-Forwarded-Proto`. The session cookie then gets `Secure` and
   HSTS is sent automatically.
5. Delete or disable the seeded `demo` endpoint.
6. Give real endpoints `hmac_sha256` authentication and a secret.
7. Leave `replay.allow_private_networks` and `replay.allow_redirects` off.
8. Only set `rate_limit.trust_forwarded_for` if a proxy you control appends the client address.
9. Set retention appropriately — 30 days by default, forever if retention is disabled.

---

## Testing

129 of the project's tests are security tests, covering invalid signatures, oversized and malformed
requests, unauthorised dashboard and API access, localhost and private-network replay, unsupported
schemes, malicious redirects, DNS rebinding, secret exposure in responses and logs, rate limiting,
request shape limits, response headers, cookie flags and CSRF.

Tests use a local stub HTTP server and disposable MongoDB containers. None touch an external service.

CI additionally exercises the SSRF guard, sign-in throttling and request limits against a real
`docker compose` stack, and runs `pip-audit` against the locked runtime dependencies.
