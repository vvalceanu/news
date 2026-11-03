import logging

from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from . import tasks
from .models import Article
from .sources import SourceUnavailable, get_source

logger = logging.getLogger(__name__)

LATEST_LIMIT = 50
PENDING_LIMIT = 100
# Per client IP. Each article runs at most once and the daily cap bounds spend; this only slows down floods.
DEEP_DIVE_RATE = "20/h"


@require_GET
def index(request):
    articles = Article.objects.order_by("-published_at")[:LATEST_LIMIT]
    return render(request, "articles/index.html", {"articles": articles, "total": Article.objects.count()})


@require_GET
def detail(request, article_id: int):
    article = get_object_or_404(Article, pk=article_id)
    return render(request, "articles/detail.html", {"article": article})


def _deep_dive_rate(group, request):
    return DEEP_DIVE_RATE  # looked up per request, so tests can lower it


@require_POST
@ratelimit(key="ip", rate=_deep_dive_rate, method="POST", block=False)
def request_deep_dive(request, article_id: int):
    """Queue the deep dive (the only way visitors make the worker spend money), then show the article."""
    article = get_object_or_404(Article, pk=article_id)
    if getattr(request, "limited", False):
        return _detail_error(request, article, "Zu viele Anfragen. Bitte später noch einmal versuchen.", 429)
    result = tasks.request_deep_dive(article.pk)
    if result is tasks.DeepDiveRequest.OVER_DAILY_CAP:
        return _detail_error(request, article, "Das Tageslimit ist erreicht. Morgen geht es weiter.", 503)
    return redirect("articles:detail", article_id=article.pk)


def _detail_error(request, article, error: str, status: int):
    return render(request, "articles/detail.html", {"article": article, "error": error}, status=status)


@require_GET
def pending(request):
    """Recent Miniflux entries we haven't stored yet, oldest first. The only page that calls Miniflux."""
    try:
        entries = tasks.pending(get_source(), limit=PENDING_LIMIT)
    except SourceUnavailable as exc:
        logger.warning("Miniflux unavailable: %s", exc)
        return render(request, "articles/pending.html", {"unavailable": True}, status=503)
    return render(
        request, "articles/pending.html", {"entries": entries, "limit": PENDING_LIMIT, "per_sweep": tasks.MAX_NEW_PER_SWEEP}
    )
