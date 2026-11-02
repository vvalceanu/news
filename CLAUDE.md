# CLAUDE.md

## Rules

- **Never commit.** Don't run `git commit`, `git push`, or anything else that writes git history. Leave changes in the working tree for the user to review and commit.
- Follow the security baseline in [`security_baseline.md`](security_baseline.md) for all code, especially Django settings, middleware, env vars, and background tasks.

## Project overview

Local news, rewritten in easy German for language learners. A Python app deployed on [Railway](https://railway.com).

## Architecture

The full architecture is in [`README.md`](README.md#architecture). Read it before changing how components interact. In short, there are three Railway services, plus one Postgres server:
- **Miniflux** fetches RSS feeds. It owns the `railway` database, which nothing else reads. It may be replaced later, so keep it behind a small interface.
- **Worker** runs [Django Q2](https://django-q2.readthedocs.io/) (`manage.py qcluster`) from the same Django project, with Postgres as the queue. See the README for the required `Q_CLUSTER` settings. It does three jobs:
  - a cron sweep that copies new Miniflux entries into our own `Article` table through the Miniflux API;
  - Haiku translation of titles;
  - the on-demand deep dive: German summaries at A2/B1, English translations and per-word vocabulary.
- **Displayer** (Django, in `news/`) lists articles with translated titles. Clicking one (a POST, never a GET) flags it DEEP_DIVE, so the expensive pipeline runs only for articles someone opens, at most once each, under a daily spending cap.

### Django project (`news/`)

One Django project holds both the displayer (web) and the worker (`qcluster`), deployed as separate Railway services from the same code.

Deployment:
- Built with Railpack; config is in `news/railpack.json`. Set the Railway service's root directory to `/news`.
- The build step runs `collectstatic`, and the start command runs `migrate` and then gunicorn on `$PORT`.
- The Django project module is `config` (`config.wsgi:application`), referenced in `railpack.json`.

Layout:
- `config/`: settings (all from env vars, see `.env.sample`), URLs, `middleware.py` (staging Basic Auth gate).
- `articles/`: the `Article` model (our copy of Miniflux entries), the sweep task in `tasks.py` (scheduled as `run_sweep` by migrations `0002`/`0004`, run by hand with `manage.py sweep`), Haiku title translation in `translation.py` (queued after each sweep, run by hand with `manage.py translate_titles`), the article list at `/` and each article's raw body at `/articles/<id>/`. Both pages read only our `Article` table, never Miniflux. `/pending/` lists recent Miniflux entries not stored yet, oldest first (a debugging aid, the only page that calls Miniflux). Only the sweep and `/pending/` talk to Miniflux, through the `NewsSource` interface in `sources.py`; `MinifluxSource` implements it via the Miniflux REST API (`MINIFLUX_URL`, `MINIFLUX_API_KEY`). Nothing reads Miniflux's database tables.
- `welcome/`: `robots.txt`.
- Admin is served at `/$DJANGO_ADMIN_URL/`, never `/admin/`.
- Postgres via `DATABASE_URL`. SQLite is only allowed locally when `DJANGO_DEBUG` is on. On Railway the displayer uses its own `displayer` database on the shared Postgres server; Miniflux owns the `railway` database.

Commands (run from `news/`):
- Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`, then `cp .env.sample .env` and fill it in.
- Run: `.venv/bin/python manage.py runserver`
- Worker: `.venv/bin/python manage.py qcluster`
- Test: `.venv/bin/pytest`. Test env vars live in `testing_env.py`, loaded as an early pytest plugin because pytest-django imports settings before `conftest.py` runs.

## Status

Proof of concept: every 15 minutes the worker copies the 20 oldest not-yet-stored Miniflux entries from the last 2 days into `Article`; then translates up to 20 titles to English with Haiku, one call per title. The displayer lists the latest 50 with their English titles and shows each one's raw body. No simplification or vocabulary yet. Update this file as the structure and commands take shape.
