"""Background tasks run by the Django Q2 cluster (`manage.py qcluster`)."""

import logging
from datetime import timedelta

from django.utils import timezone
from django_q.tasks import async_task

from . import translation
from .models import Article
from .sources import NewsSource, get_source
from .translation import anthropic_client

logger = logging.getLogger(__name__)

SWEEP_SCHEDULE_NAME = "sweep-miniflux"
SWEEP_EVERY_MINUTES = 15
# Never import entries older than this: keeps the first sweep, and feeds newly added to
# Miniflux (which arrive with their whole backlog), from flooding the table.
MAX_AGE = timedelta(days=2)
# Each sweep adds at most this many articles, oldest first; later sweeps take the rest.
MAX_NEW_PER_SWEEP = 20
# Each translation run translates at most this many titles (one Haiku call each).
TRANSLATE_PER_RUN = 20
PAGE_SIZE = 100
MAX_PAGES = 20  # safety stop on how far one sweep pages through Miniflux


def run_sweep():
    """What the schedule runs: a sweep, then title translation as its own task.

    Translation is queued even when the sweep fails, so titles still get translated while
    Miniflux is down.
    """
    try:
        return sweep()
    finally:
        async_task("articles.tasks.translate_titles")


def translate_titles(client=None, limit: int | None = None) -> int:
    """Translate up to TRANSLATE_PER_RUN untranslated titles with Haiku. Returns how many were translated."""
    client = client or anthropic_client()  # fails on a missing key before any article is touched
    return translation.translate_titles(client, limit or TRANSLATE_PER_RUN)


def sweep(source: NewsSource | None = None) -> int:
    """Copy the oldest entries we haven't stored yet (at most MAX_NEW_PER_SWEEP) into `Article`.

    Every sweep re-reads the whole MAX_AGE window from the source instead of continuing from a
    cursor, so an entry that showed up late, or that an earlier sweep missed, is still picked up.
    Returns how many articles were added.
    """
    source = source or get_source()
    entries = pending(source, limit=MAX_NEW_PER_SWEEP)
    # ignore_conflicts covers a concurrent sweep inserting the same entry first.
    Article.objects.bulk_create(
        [
            Article(
                miniflux_id=entry.id,
                title=entry.title,
                url=entry.url,
                feed_title=entry.feed_title,
                published_at=entry.published_at,
                content=entry.content,
            )
            for entry in entries
        ],
        ignore_conflicts=True,
    )
    logger.info("Sweep added %d articles", len(entries))
    return len(entries)


def pending(source: NewsSource, limit: int):
    """Entries from the last MAX_AGE that we haven't stored yet, oldest first, at most `limit`."""
    published_after = timezone.now() - MAX_AGE
    found = []
    seen = set()
    for page in range(MAX_PAGES):
        entries = source.entries_published_after(published_after, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
        ids = [entry.id for entry in entries]
        stored = set(Article.objects.filter(miniflux_id__in=ids).values_list("miniflux_id", flat=True))
        for entry in entries:
            if entry.id not in stored and entry.id not in seen:
                seen.add(entry.id)
                found.append(entry)
        if len(found) >= limit or len(entries) < PAGE_SIZE:
            break
    return found[:limit]
