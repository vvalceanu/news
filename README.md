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
      - **Words:** after both calls, each German summary is split into words with lemmas (`Article.words_b1`, `words_a2`, JSON lists). No API calls, so it's free.
        - spaCy (`de_core_news_sm`, NER excluded) tokenizes, tags and parses. simplemma gives the lemmas, using spaCy's part of speech to pick the casing. A separable particle (dependency `svp`) is glued onto its verb's lemma (`teilte … mit` → `mitteilen`), and the particle points to its verb. If the glued verb isn't in simplemma's dictionary, spaCy's own lemma is tried instead.
        - Each word also stores spaCy's tag, dependency and morphology (a Universal Dependencies FEATS string such as `Mood=Sub|Tense=Pres`). Joining every word's text and trailing whitespace gives back the summary exactly, so a page can render from the words alone.
        - It runs as a subprocess (`articles/words.py` starts `articles/words_subprocess.py`), because spaCy and simplemma's dictionary take about 400 MB and Python wouldn't hand that back to the system. The process exits after about a second, so Railway bills that memory for seconds, not all day. The child doesn't import Django and gets no env vars, so it sees no secrets and no database. It is killed after 60 s.
        - A failure leaves the words empty and is only logged. `manage.py analyze_words <id> [<id> ...]` (re)analyzes articles that already have summaries.
        - The article page renders each summary from its words (see **Reader** below), with a debug section under it.
      - **Dictionary** (the `dictionary` app): the German entries of English Wiktionary, as extracted by [wiktextract](https://github.com/tatuylonen/wiktextract) and published on [kaikki.org](https://kaikki.org/). The license is CC BY-SA 4.0, so every page's footer credits it.
        - `dictionary.Entry`: one row per word, word class and origin (`Bank` the bench and `Bank` the bank are two rows). It holds the meanings in Wiktionary's order, main meaning first, plus gender and plural for nouns, and a `swiss` flag (Swiss words like `Velo` are German entries tagged `Switzerland`). About 95,000 rows.
        - `dictionary.Redirect`: words whose entry only points elsewhere. These are inflected forms (`Stellen` → `Stelle`), contractions (`im` → `in`) and the Swiss ss spellings (`Strasse` → `Straße`). About 280,000 rows.
        - `manage.py fetch_wiktionary ../data/wiktionary-de.jsonl.gz` streams kaikki.org's full dump (about 3 GB, all languages; the German-only file there is deprecated) and keeps the German entries, about 70 MB gzipped. It takes about 4 minutes and about 150 MB of memory. The data is updated weekly. `data/` is in git, but its `.gitignore` ignores everything else in it, so the file is never committed.
        - `manage.py import_wiktionary <file>` replaces both tables in one transaction. That takes about 12 s and 200 MB, and a bad file leaves the old dictionary in place.
        - **On Railway:** deploy first, so that the displayer's `migrate` creates the tables. Then run both commands inside the worker. The download is free inbound traffic, the database is reached over the private network, and nothing has to be uploaded or exposed:
          ```sh
          railway ssh -s <worker service> -- sh -c 'python manage.py fetch_wiktionary /tmp/de.jsonl.gz && python manage.py import_wiktionary /tmp/de.jsonl.gz && rm /tmp/de.jsonl.gz'
          ```
          This adds about 150 MB to the worker's memory for the 4 minutes of the download, then about 200 MB for the 12 s import. Rerun it whenever a fresher dictionary is wanted.
        - **Lookup** (`dictionary/lookup.py`), when the page renders, so a fresh import shows up without reanalyzing articles. It takes three queries per summary. For each word it tries the lemma first. Then it adds the ß word for a Swiss ss spelling, without replacing the lemma, because `Masse` is a word of its own and also the Swiss spelling of `Maße`. If nothing of the right word class has matched, it tries variants and contractions (`im` → `in`). If still nothing matched, and the word isn't a name, it tries the word an inflected form points to, then the lemma with its first letter's case flipped. It keeps the entries whose word class fits spaCy's part of speech, or all of them if none fits. Punctuation, numbers and separable particles aren't looked up; a particle's verb row has the match.
      - **Reader** (`articles/reading.py`, `templates/articles/_reader.html`, `static/articles/reader.js`): each summary is rendered from its words, split into paragraphs at blank lines. Every word with a lemma is clickable but reads as plain text. Clicking one marks it, and both halves of a separable verb, with the highlighter, and puts its details where the English translation was: the dictionary form, the part of speech and grammar (spaCy's morphology in words, such as "3rd person, singular, past, indicative"), and each dictionary entry with its article, plural and all its meanings. A large × or Esc brings the English back. On phones the details are a sheet over the bottom of the screen instead.
        - The details reach the page once, as JSON in a `json_script` tag, with each dictionary entry listed once. The script builds the panel with `textContent` only, so dictionary text can't inject markup. Without JavaScript the words are plain text next to the English.
        - Under each summary, a closed **Debug** section shows the summary as Claude wrote it (to check the rendering against) and the word table with each word's dictionary matches.
        - **Ask AI** (`articles/word_questions.py`): a button in the details panel asks Haiku (`claude-haiku-5-5`, effort `low`) "In the sentence "…", what does "…" mean? Answer in English. If there are several possible interpretations, list them all." A separable verb is asked about whole (`teilte … mit`).
          - **No prompt injection through the API:** the browser sends only the word's position (`POST /articles/<id>/words/<level>/<index>/ask/`). The server takes the word and its sentence (cut at `.` `!` `?` `:` `;` and blank lines) from its own stored words, so Haiku only ever sees summaries Sonnet wrote. Positions that aren't words (punctuation, numbers, out of range), levels other than A2/B1, and articles without a finished deep dive get a 404.
          - **The worker makes the call; the displayer has no API key.** The POST saves the question (`articles.WordQuestion`, one per article, level and word) as `waiting` and queues `articles.word_questions.answer_question` (202). The worker asks Haiku and marks it `done` with the answer, or `failed`. The reader polls the same URL with GET every second, for up to 3 minutes, since a question can wait behind a running deep dive. A second click while a question is waiting doesn't queue it again. A question still `waiting` after 10 minutes is taken as lost (e.g. the worker restarted) and can be asked again.
          - **The answer is plain text**, saved as is and always shown escaped (`textContent`, line breaks kept by CSS). Anyone opening the word later sees the saved answer, with no new call.
          - **Limits:** the POST needs the CSRF token (sent as `X-CSRFToken`) and is limited to 60 per hour per client IP; polling is read-only and not limited. The daily cap applies: each question's cost (about $0.0002) is saved with the time it was asked, and `spent_today()` adds today's questions to today's deep dives. The displayer checks the cap before queueing (503 once it's reached; saved answers still show), and the worker checks again before calling.
          - **Failures:** a call that fails (API error, no usable answer, no API key, cap reached meanwhile) leaves the question `failed`, and the reader says "Couldn't get an answer. Try again."; its cost still counts. It is never retried automatically.

3. **Displayer** renders a list of articles and their translated titles, read from our own tables. When the user clicks on an article, it is marked as `DEEP_DIVE` and a task is scheduled for the worker, so the pipeline only runs for articles the user is interested in.
   - The deep dive is requested by a POST (the "Zusammenfassen" button, `POST /articles/<id>/deep-dive/`), never by a plain GET link, so crawlers and link previews can't trigger LLM calls. The page reloads every 10 s until the summaries are there.
   - The POST is rate-limited per client IP (`django-ratelimit`, 20/hour). The counters live in Postgres (`DatabaseCache`, created by `createcachetable` in the start command), because there's no Redis. That cache increments non-atomically, so a burst can slip a few requests past the limit. That's acceptable because the daily cap, not the rate limit, bounds spending.
   - The client IP comes from Railway's `X-Real-IP` header (`config.middleware.RealClientIPMiddleware`). If Railway ever passes through a client-supplied value, an attacker could dodge the per-IP limit, but not the daily cap.
