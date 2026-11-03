"""Background tasks run by the Django Q2 cluster (`manage.py qcluster`)."""

import logging
from datetime import timedelta
from decimal import Decimal
from enum import Enum

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import F, Sum
from django.utils import timezone
from django_q.tasks import async_task

from . import deep_dive as dd
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
# Deep dive: two Sonnet calls in a row must fit in Q_CLUSTER's timeout (600 s), retries included.
DEEP_DIVE_TIMEOUT_SECONDS = 120.0
# Rough cost of one deep dive. Queued and running ones count this much against the daily cap
# until their real cost is known, so a burst of clicks can't overshoot it by much.
DEEP_DIVE_ESTIMATE_USD = Decimal("0.10")


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


class DeepDiveRequest(Enum):
    QUEUED = "queued"
    ALREADY_REQUESTED = "already requested"
    OVER_DAILY_CAP = "over daily cap"


def spent_today() -> Decimal:
    """What deep dives requested since local midnight have cost so far."""
    midnight = timezone.localtime(timezone.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    total = Article.objects.filter(deep_dive_requested_at__gte=midnight).aggregate(total=Sum("deep_dive_cost_usd"))
    return total["total"] or Decimal(0)


def request_deep_dive(article_id: int) -> DeepDiveRequest:
    """Queue the deep dive for an article that never had one, unless today's cap is (nearly) used up."""
    in_flight = Article.objects.filter(
        deep_dive_status__in=[Article.DeepDive.QUEUED, Article.DeepDive.RUNNING]
    ).count()
    if spent_today() + in_flight * DEEP_DIVE_ESTIMATE_USD >= settings.DEEP_DIVE_DAILY_CAP_USD:
        return DeepDiveRequest.OVER_DAILY_CAP
    queued = Article.objects.filter(pk=article_id, deep_dive_status=Article.DeepDive.NONE).update(
        deep_dive_status=Article.DeepDive.QUEUED, deep_dive_requested_at=timezone.now()
    )
    if not queued:
        return DeepDiveRequest.ALREADY_REQUESTED
    async_task("articles.tasks.deep_dive", article_id)  # pass the id: Q2 pickles task arguments
    return DeepDiveRequest.QUEUED


def deep_dive(article_id: int, client=None) -> bool:
    """Write the B1 and A2 summaries of a queued article. Returns whether it ran.

    The article is marked DONE whatever happens once a call may have been made, so it never
    runs twice by accident. A level whose call fails is stored as "".
    """
    try:
        client = client or anthropic_client(timeout=DEEP_DIVE_TIMEOUT_SECONDS, max_retries=1)
    except ImproperlyConfigured:
        _release(article_id)
        raise
    if spent_today() >= settings.DEEP_DIVE_DAILY_CAP_USD:
        logger.warning("Deep dive for article %s skipped: daily cap reached", article_id)
        _release(article_id)
        return False
    claimed = Article.objects.filter(pk=article_id, deep_dive_status=Article.DeepDive.QUEUED).update(
        deep_dive_status=Article.DeepDive.RUNNING
    )
    if not claimed:
        return False  # not queued, or another worker took it
    try:
        article = Article.objects.get(pk=article_id)
        markdown = dd.article_markdown(article.content)
        for level in dd.LEVELS:
            _summarize_level(client, article, markdown, level)
    finally:
        Article.objects.filter(pk=article_id).update(deep_dive_status=Article.DeepDive.DONE)
    return True


def _summarize_level(client, article, markdown: str, level: str):
    german = english = ""
    cost = Decimal(0)
    if not markdown:
        logger.warning("Deep dive for article %s: empty body, nothing to summarize", article.pk)
    else:
        try:
            summary = dd.summarize(client, article.title, markdown, level)
            german, english, cost = summary.german, summary.english, summary.cost_usd
        except dd.SummaryFailed as exc:
            cost = exc.cost_usd
            logger.warning("Deep dive %s failed for article %s: %s", level, article.pk, exc)
        except dd.CALL_ERRORS as exc:
            logger.warning("Deep dive %s failed for article %s: %s", level, article.pk, type(exc).__name__)
    # Saved after each call, so a concurrent cap check already sees this call's cost.
    Article.objects.filter(pk=article.pk).update(
        **{f"summary_{level.lower()}": german, f"summary_{level.lower()}_en": english},
        deep_dive_cost_usd=F("deep_dive_cost_usd") + cost,
    )


def _release(article_id: int):
    """Put a queued article back, so it can be requested again (e.g. tomorrow, under a fresh cap)."""
    Article.objects.filter(pk=article_id, deep_dive_status=Article.DeepDive.QUEUED).update(
        deep_dive_status=Article.DeepDive.NONE, deep_dive_requested_at=None
    )
