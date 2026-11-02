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
| `ANTHROPIC_API_KEY` | worker only | Claude API key for title translation. The displayer doesn't need it. |


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

   3. **Deep dive.** For articles flagged as `DEEP_DIVE`:
      - Atomically mark the article as "in process".
      - Invoke Opus / Sonnet to summarize it at several German levels, e.g. A2 and B1, and save the summaries sentence by sentence. Another Sonnet call then translates the sentences to English. Each sentence of a summary is stored as `{summarized sentence in German at the desired level, translated sentence in English}`.
      - Parse each sentence into words, and use Haiku to translate each non-trivial word to English. This is saved as part of the summary (an article can have more than one summary).
      - Mark the article as "processed".

3. **Displayer** renders a list of articles and their translated titles, read from our own tables. When the user clicks on an article, it is marked as `DEEP_DIVE` and a task is scheduled for the worker, so the pipeline only runs for articles the user is interested in.
   - The `DEEP_DIVE` flag is set by a POST (a button, or a POST the article page sends after it loads), never by a plain GET link, so crawlers and link previews can't trigger LLM calls.
   - Each article goes through the deep-dive pipeline at most once.
   - The worker enforces a daily LLM spending cap and stops scheduling deep dives once it's reached.
