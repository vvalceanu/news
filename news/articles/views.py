import logging

from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from . import tasks
from .models import Article
from .sources import SourceUnavailable, get_source

logger = logging.getLogger(__name__)

LATEST_LIMIT = 50
PENDING_LIMIT = 100


@require_GET
def index(request):
    articles = Article.objects.order_by("-published_at")[:LATEST_LIMIT]
    return render(request, "articles/index.html", {"articles": articles, "total": Article.objects.count()})


@require_GET
def detail(request, article_id: int):
    article = get_object_or_404(Article, pk=article_id)
    return render(request, "articles/detail.html", {"article": article})


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
