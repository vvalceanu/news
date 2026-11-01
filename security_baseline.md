## Security baseline

### Secrets & env
- All secrets come from env vars (`python-dotenv` locally); `.env` is gitignored, `.env.sample` is committed with empty values plus a comment on where to get each key.
- Fail fast: raise `ImproperlyConfigured` if `DJANGO_SECRET_KEY` is missing. Never fall back to a default key.
- `DEBUG` defaults to False and is only on when the env var is explicitly `true/1/yes`.
- `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` are comma-separated env lists; no wildcard.
- Every new env var goes into both `.env.sample` and README.

### HTTPS, cookies, proxy
- Behind a proxy: `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`.
- `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE = not DEBUG`, `SESSION_COOKIE_HTTPONLY = True`, `SAMESITE = "Lax"`, explicit `SESSION_COOKIE_AGE`.
- Keep SecurityMiddleware, CsrfViewMiddleware and XFrameOptionsMiddleware in the stack.
- Behind Cloudflare, a small middleware copies `CF-Connecting-IP` into `REMOTE_ADDR` so rate limits and logs see the real client IP.
- Gaps to close from day one: set `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS` and `SECURE_CONTENT_TYPE_NOSNIFF` in Django rather than relying on Cloudflare, and serve admin at a non-default path instead of `/admin/`.

### Staging
- Gate all of staging behind HTTP Basic Auth middleware, on only when both user and pass env vars are set (a no-op in prod). This also keeps crawlers from indexing staging.
- Compare credentials with `secrets.compare_digest` (catch `TypeError` on non-ASCII input). Exempt only paths that authenticate another way, such as signed webhooks.
- Debug tools need an explicit flag and are also hard-blocked when `RAILWAY_ENVIRONMENT == "production"`.

### Data protection
- PII and sensitive fields use a Fernet-encrypted model field, with the key from `FIELD_ENCRYPTION_KEY`. Raise if it's missing; never store plaintext silently.
- Encrypted fields can't be queried: look up by id and decrypt in Python.
- Sentry: `send_default_pii=False`, `traces_sample_rate=0`, plus a `before_send` that strips request bodies on endpoints that take PII.

### Request handling
- Rate-limit every public POST by IP with `django-ratelimit` (`RATELIMIT_ENABLE = not DEBUG`, but forced on in tests).
- Validate redirect targets (including the Referer) with `url_has_allowed_host_and_scheme()`, not a hand-rolled urlparse.
- Webhooks: `@csrf_exempt` + `@require_POST`, and always verify the provider's signature (`stripe.Webhook.construct_event`); return 400 if it fails.
- Download links for private files use `TimestampSigner` with a dedicated salt and a `max_age` set from env.
- `robots.txt` disallows `/admin/`.

### Tests & ops
- Tests use a hardcoded test-only Fernet key set in `conftest.py` before settings load. Dev/debug flags are forced off in an autouse fixture.
- Background tasks use `max_attempts: 1` with "fail → mark status, no auto-retry", so paid or LLM calls never repeat silently.
- `django` logger at ERROR and logs to the console only; no request bodies in logs.
