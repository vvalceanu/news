import logging

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .sources import ArticleNotFound, SourceUnavailable, get_source

logger = logging.getLogger(__name__)

LATEST_LIMIT = 50


def _unavailable(request, exc):
    logger.warning("News source unavailable: %s", exc)
    return render(request, "articles/unavailable.html", status=503)


@require_GET
def index(request):
    try:
        articles = get_source().latest(LATEST_LIMIT)
    except SourceUnavailable as exc:
        return _unavailable(request, exc)
    return render(request, "articles/index.html", {"articles": articles})


@require_GET
def detail(request, article_id: int):
    try:
        article = get_source().get(article_id)
    except ArticleNotFound:
        raise Http404("Article not found")
    except SourceUnavailable as exc:
        return _unavailable(request, exc)
    return render(request, "articles/detail.html", {"article": article})
