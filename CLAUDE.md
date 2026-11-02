# CLAUDE.md

## Rules

- **Never commit.** Don't run `git commit`, `git push`, or anything else that writes git history. Leave changes in the working tree for the user to review and commit.
- Follow the security baseline in [`security_baseline.md`](security_baseline.md) for all code, especially Django settings, middleware, env vars, and background tasks.

## Project overview

Local news, rewritten in easy German for language learners. A Python app deployed on [Railway](https://railway.com).

## Architecture

Two components, deployed as separate Railway services that share one **PostgreSQL** database.

### Worker
- Aggregates news articles and writes them to Postgres.
- Source: **Miniflux** (RSS/feed reader) to start with. It may be replaced with something else if it turns out not to be enough, so keep the ingestion layer behind a small interface rather than coupling the rest of the code to Miniflux.

### Displayer (`displayer/`)
A **Django** app that shows each article with:
1. **German version**: translated and simplified to CEFR level **A2 / B1**.
2. **English translation** of the article.
3. **Vocabulary list**: a definition for each word.

Deployment:
- Built with Railpack; config is in `displayer/railpack.json`. Set the Railway service's root directory to `displayer/`.
- The build step runs `collectstatic`, and the start command runs `migrate` and then gunicorn on `$PORT`.
- The Django project module is `config` (`config.wsgi:application`), referenced in `railpack.json`.

Layout:
- `config/`: settings (all from env vars, see `.env.sample`), URLs, `middleware.py` (staging Basic Auth gate).
- `articles/`: the article list at `/` and each article's raw body at `/articles/<id>/`. Articles come from `sources.py`: views only use the `NewsSource` interface; `MinifluxSource` implements it via the Miniflux REST API (`MINIFLUX_URL`, `MINIFLUX_API_KEY`). The displayer never reads Miniflux's database tables.
- `welcome/`: `robots.txt`.
- Admin is served at `/$DJANGO_ADMIN_URL/`, never `/admin/`.
- Postgres via `DATABASE_URL`. SQLite is only allowed locally when `DJANGO_DEBUG` is on. On Railway the displayer uses its own `displayer` database on the shared Postgres server; Miniflux owns the `railway` database.

Commands (run from `displayer/`):
- Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`, then `cp .env.sample .env` and fill it in.
- Run: `.venv/bin/python manage.py runserver`
- Test: `.venv/bin/pytest`. Test env vars live in `testing_env.py`, loaded as an early pytest plugin because pytest-django imports settings before `conftest.py` runs.

## Status

Proof of concept: the displayer lists the latest Miniflux entries and shows each one's raw body. No translation, simplification or vocabulary yet. The worker doesn't exist yet. Update this file as the structure and commands take shape.
