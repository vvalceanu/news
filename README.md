# news
Local news, translated to easy german

## Displayer environment variables

See `news/.env.sample` for details and examples.

| Variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | yes | Django secret key. The app refuses to start without it. |
| `DJANGO_DEBUG` | no | `true`/`1`/`yes` turns debug on. Off by default. |
| `DJANGO_ALLOWED_HOSTS` | yes (prod) | Comma-separated hostnames, no wildcard. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | yes (prod) | Comma-separated full origins, e.g. `https://news.example.com`. |
| `DJANGO_ADMIN_URL` | yes | Admin path without slashes. Must not be `admin`. |
| `DATABASE_URL` | yes (prod) | Postgres URL. Optional locally in debug mode, where it falls back to SQLite. |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | staging only | When both are set, the whole site requires HTTP Basic Auth. |
| `MINIFLUX_URL` / `MINIFLUX_API_KEY` | yes | Miniflux REST API, used by the sweep and `/pending/`. |
| `ANTHROPIC_API_KEY` | worker only | Claude API key for title translation and deep dives. The displayer doesn't need it. |
| `DEEP_DIVE_DAILY_CAP_USD` | no | Daily spending cap for deep dives, in USD. Default `2`. Set it on both the displayer and the worker. |


# Architecture

1. **Miniflux** (a separate Railway container) fetches articles from a set of RSS feeds.

2. **Worker** (not named yet, probably `worker`) does three kinds of processing.

   It runs on [Django Q2](https://django-q2.readthedocs.io/) (`django-q2`, the maintained fork of the abandoned `django-q`). It runs from the same Django project as the displayer, so both share the models. It is deployed as its own Railway service whose start command is `manage.py qcluster`.
   - **Broker:** the Django ORM, i.e. our Postgres. No Redis.
   - **Schedules:** the sweep is a Q2 schedule (every 15 minutes) created by the data migration `articles/migrations/0002_schedule_sweep.py`, so `migrate` on deploy turns it on. No Railway cron service is needed. `manage.py sweep` runs one by hand.
   - **Railway:** the worker service uses the same root directory (`/news`) and env vars as the displayer, with its start command overridden to `python manage.py qcluster`. Only the displayer runs `migrate`.
   - **Deep dives:** the displayer queues them with `async_task(...)`. The cluster checks the queue every `poll` (0.2 s by default), so they start almost immediately.
   - **Task arguments:** tasks are pickled, so pass the article id and load the article inside the task.
   - **Settings to keep right** (`Q_CLUSTER`):
     - `timeout` must cover the longest deep dive, e.g. 600 s.
     - `retry` must be larger than `timeout`, e.g. 900 s. Otherwise Q2 re-delivers a task that is still running.
     - `max_attempts` must be set. The default `0` means unlimited retries, which means unlimited LLM spend. We use `1` with `ack_failures: True`: a failed task is recorded and dropped, never re-run (see `security_baseline.md`).
     - `catch_up` is `False`, so after downtime a missed schedule runs once, not once per missed slot.
     - `workers` must be set explicitly (e.g. 2). It defaults to the host's CPU count.

   1. **Sweep.** Copy new articles from Miniflux into our own `Article` table through the Miniflux REST API. This runs every 15 minutes.
      - Each sweep asks Miniflux for entries published in the last 2 days (`MAX_AGE`), oldest first (`GET /v1/entries?order=published_at&direction=asc&published_after=...`, paged with `offset`), drops the ones we already stored, and adds the oldest 20 (`MAX_NEW_PER_SWEEP`). Later sweeps take the rest.
      - There is no id cursor: every sweep re-reads the whole 2-day window (about 120 entries, ~1 MB, at current volume). So an entry that appears late, or that an earlier sweep missed, is still picked up.
      - The Miniflux entry id is a unique key, so re-running a sweep, or two overlapping sweeps, never creates duplicates.
      - If feeds ever produce more than ~20 entries per 15 minutes, oldest-first plus the cap means we fall behind and entries age out of the window before they are imported. Raise `MAX_NEW_PER_SWEEP` if that happens.
      - Everything after this step (title translation, deep dives, the displayer) reads only our own tables, never Miniflux. Miniflux stays behind a small interface so it can be replaced.
      - The `/pending/` page lists the entries not stored yet, oldest first, for debugging.

   2. **Title translation.** After every sweep (even a failed one), `run_sweep` queues a `translate_titles` task. It takes up to 20 untranslated titles, newest first, and translates each one with its own Haiku call (`claude-haiku-5-5`, low effort), storing the result in `Article.title_en`. `manage.py translate_titles` runs it by hand.
      - Each title gets one attempt (`title_en_attempts`), counted before the call. A failure tied to the title (refusal, empty answer, 4xx) is logged and not retried; the page shows the German title only. Set the attempts back to 0 to try again.
      - Errors that aren't about the title (network, 429, 5xx, bad key) stop the run and leave the titles for the next one.
      - Cost: roughly 200 input and 30 output tokens per title, a few cents a month at current volume. The prompt is too short (<512 tokens) for prompt caching.

   3. **Deep dive.** Two German summaries of an article, at B1 and at A2, each with an English translation for reference. It runs only for articles someone asked for, and at most once each.
      - **States** (`Article.deep_dive_status`): not requested → `queued` (the displayer's POST) → `running` (the worker claims it with a conditional update) → `done`.
      - **Input:** the stored HTML body is converted to Markdown (`markdownify`). Links, images, scripts and styles are dropped, which saves tokens and leaves less room for prompt injection.
      - **Calls:** one `claude-sonnet-5-5` call per level (adaptive thinking, effort `medium`, structured output `{german, english}`), so the English always belongs to the German written in the same call. The only difference between the levels is vocabulary. The level descriptions live in `articles/prompts/level_b1.txt` and `level_a2.txt`. Grammar is left natural on purpose. The German is Swiss Standard German (Schweizer Hochdeutsch): Swiss vocabulary, and always `ss`, never `ß`. Any `ß` that slips through is replaced in code.
      - **Failures:** a level whose call fails (refusal, `max_tokens`, bad JSON, any API error) is stored as `""`, and the other level still runs. The article is `done` whatever happens once a call may have been made, so it is never re-run by accident. `manage.py deep_dive <id> --force` redoes one.
      - **Daily cap** (`DEEP_DIVE_DAILY_CAP_USD`, default $2): each call's cost is computed from its token counts at list prices and added to `Article.deep_dive_cost_usd`. The displayer refuses new requests once today's spend, plus $0.10 for each queued or running deep dive, reaches the cap. The worker checks again before calling and puts the article back if the cap is already spent. "Today" starts at midnight Europe/Berlin.
      - **Cost:** roughly $0.03–0.07 per level (an estimate; check `deep_dive_cost_usd` on real articles).
      - Later: split into sentences and translate the vocabulary with Haiku.

3. **Displayer** renders a list of articles and their translated titles, read from our own tables. When the user clicks on an article, it is marked as `DEEP_DIVE` and a task is scheduled for the worker, so the pipeline only runs for articles the user is interested in.
   - The deep dive is requested by a POST (the "Zusammenfassen" button, `POST /articles/<id>/deep-dive/`), never by a plain GET link, so crawlers and link previews can't trigger LLM calls. The page reloads every 10 s until the summaries are there.
   - The POST is rate-limited per client IP (`django-ratelimit`, 20/hour). The counters live in Postgres (`DatabaseCache`, created by `createcachetable` in the start command), because there's no Redis. That cache increments non-atomically, so a burst can slip a few requests past the limit. That's acceptable because the daily cap, not the rate limit, bounds spending.
   - The client IP comes from Railway's `X-Real-IP` header (`config.middleware.RealClientIPMiddleware`). If Railway ever passes through a client-supplied value, an attacker could dodge the per-IP limit, but not the daily cap.
