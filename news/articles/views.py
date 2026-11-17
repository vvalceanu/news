import logging

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit

from . import reading, tasks, word_questions
from .models import Article, WordQuestion
from .sources import SourceUnavailable, get_source

logger = logging.getLogger(__name__)

LATEST_LIMIT = 50
PENDING_LIMIT = 100
# Per client IP. Each article runs at most once and the daily cap bounds spend; this only slows down floods.
DEEP_DIVE_RATE = "20/h"
# Per client IP. Each word is asked about at most once (answers are saved) and the daily cap
# bounds spend; this only slows down someone clicking through every word.
ASK_WORD_RATE = "60/h"


@require_GET
def index(request):
    articles = Article.objects.order_by("-published_at")[:LATEST_LIMIT]
    return render(request, "articles/index.html", {"articles": articles, "total": Article.objects.count()})


@require_GET
def detail(request, article_id: int):
    article = get_object_or_404(Article, pk=article_id)
    return render(request, "articles/detail.html", _detail_context(article))


def _detail_context(article: Article, **extra) -> dict:
    # Each summary as clickable words, with each word's details (grammar, dictionary).
    answers = {(q.level, q.index): q.answer for q in WordQuestion.objects.filter(article=article).exclude(answer="")}
    readers = {
        level: reading.build(
            getattr(article, f"words_{level}"), {i: a for (lvl, i), a in answers.items() if lvl == level}
        )
        for level in ("a2", "b1")
    }
    return {"article": article, "readers": readers, **extra}


def _deep_dive_rate(group, request):
    return DEEP_DIVE_RATE  # looked up per request, so tests can lower it


@require_POST
@ratelimit(key="ip", rate=_deep_dive_rate, method="POST", block=False)
def request_deep_dive(request, article_id: int):
    """Queue the deep dive (the only way visitors make the worker spend money), then show the article."""
    article = get_object_or_404(Article, pk=article_id)
    if getattr(request, "limited", False):
        return _detail_error(request, article, "Too many requests. Please try again later.", 429)
    result = tasks.request_deep_dive(article.pk)
    if result is tasks.DeepDiveRequest.OVER_DAILY_CAP:
        return _detail_error(request, article, "The daily limit is reached. Please come back tomorrow.", 503)
    return redirect("articles:detail", article_id=article.pk)


def _ask_word_rate(group, request):
    return ASK_WORD_RATE  # looked up per request, so tests can lower it


@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate=_ask_word_rate, method="POST", block=False)
def ask_word(request, article_id: int, level: str, index: int):
    """"Ask AI" in the reader. POST asks what word `index` of a summary means in its sentence;
    the worker answers, and the reader polls with GET until it has. The browser names the word
    only by position; word_questions takes the word and sentence from our data."""
    article = get_object_or_404(Article, pk=article_id)
    if getattr(request, "limited", False):
        return JsonResponse({"error": "Too many questions. Please try again later."}, status=429)
    try:
        if request.method == "POST":
            state = word_questions.request_answer(article, level, index)
        else:
            state = word_questions.current_state(article, level, index)
    except word_questions.Refused as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    return JsonResponse(state, status=202 if state["status"] == "waiting" and request.method == "POST" else 200)


def _detail_error(request, article, error: str, status: int):
    return render(request, "articles/detail.html", _detail_context(article, error=error), status=status)


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
