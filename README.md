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


# Architecture

1. **Miniflux** (a separate Railway container) fetches articles from a set of RSS feeds.

2. **Worker** (not named yet, probably `worker`) does three kinds of processing.

   It runs on [Django Q2](https://django-q2.readthedocs.io/) (`django-q2`, the maintained fork of the abandoned `django-q`). It runs from the same Django project as the displayer, so both share the models. It is deployed as its own Railway service whose start command is `manage.py qcluster`.
   - **Broker:** the Django ORM, i.e. our Postgres. No Redis.
   - **Schedules:** the sweep and title translation are Q2 schedules with cron expressions. No Railway cron service is needed.
   - **Deep dives:** the displayer queues them with `async_task(...)`. The cluster checks the queue every `poll` (0.2 s by default), so they start almost immediately.
   - **Task arguments:** tasks are pickled, so pass the article id and load the article inside the task.
   - **Settings to keep right** (`Q_CLUSTER`):
     - `timeout` must cover the longest deep dive, e.g. 600 s.
     - `retry` must be larger than `timeout`, e.g. 900 s. Otherwise Q2 re-delivers a task that is still running.
     - `max_attempts` must be set (e.g. 3). The default `0` means unlimited retries, which means unlimited LLM spend.
     - `workers` must be set explicitly (e.g. 2). It defaults to the host's CPU count.

   1. **Sweep.** Copy new articles from Miniflux into our own `Article` table through the Miniflux REST API (`GET /v1/entries` with `after_entry_id` set to the highest Miniflux entry id we already stored). This runs on a regular schedule.
      - The Miniflux entry id is a unique key, so re-running a sweep, or two overlapping sweeps, never creates duplicates.
      - The very first sweep uses `published_after`, so we don't import the whole history.
      - Everything after this step (title translation, deep dives, the displayer) reads only our own tables, never Miniflux. Miniflux stays behind a small interface so it can be replaced.

   2. **Title translation.** For every new article, invoke Haiku to translate the title from German to English. A regular schedule picks up the most recent 20 articles whose titles aren't translated yet.

   3. **Deep dive.** For articles flagged as `DEEP_DIVE`:
      - Atomically mark the article as "in process".
      - Invoke Opus / Sonnet to summarize it at several German levels, e.g. A2 and B1, and save the summaries sentence by sentence. Another Sonnet call then translates the sentences to English. Each sentence of a summary is stored as `{summarized sentence in German at the desired level, translated sentence in English}`.
      - Parse each sentence into words, and use Haiku to translate each non-trivial word to English. This is saved as part of the summary (an article can have more than one summary).
      - Mark the article as "processed".

3. **Displayer** renders a list of articles and their translated titles, read from our own tables. When the user clicks on an article, it is marked as `DEEP_DIVE` and a task is scheduled for the worker, so the pipeline only runs for articles the user is interested in.
   - The `DEEP_DIVE` flag is set by a POST (a button, or a POST the article page sends after it loads), never by a plain GET link, so crawlers and link previews can't trigger LLM calls.
   - Each article goes through the deep-dive pipeline at most once.
   - The worker enforces a daily LLM spending cap and stops scheduling deep dives once it's reached.
