# news
Local news, translated to easy german

## Displayer environment variables

See `displayer/.env.sample` for details and examples.

| Variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | yes | Django secret key. The app refuses to start without it. |
| `DJANGO_DEBUG` | no | `true`/`1`/`yes` turns debug on. Off by default. |
| `DJANGO_ALLOWED_HOSTS` | yes (prod) | Comma-separated hostnames, no wildcard. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | yes (prod) | Comma-separated full origins, e.g. `https://news.example.com`. |
| `DJANGO_ADMIN_URL` | yes | Admin path without slashes. Must not be `admin`. |
| `DATABASE_URL` | yes (prod) | Postgres URL. Optional locally in debug mode, where it falls back to SQLite. |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | staging only | When both are set, the whole site requires HTTP Basic Auth. |
