"""Where articles come from.

The rest of the displayer only talks to `NewsSource`, so Miniflux can be swapped for
another feed backend without touching views or templates.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import miniflux
import requests
from django.conf import settings


@dataclass(frozen=True)
class Article:
    id: int
    title: str
    url: str
    feed_title: str
    published_at: datetime
    content: str


class ArticleNotFound(Exception):
    pass


class SourceUnavailable(Exception):
    """The backend could not be reached or answered with an error."""


class NewsSource(Protocol):
    def latest(self, limit: int) -> list[Article]: ...

    def get(self, article_id: int) -> Article: ...


class MinifluxSource:
    TIMEOUT_SECONDS = 10.0

    def __init__(self, base_url: str, api_key: str, client=None):
        self._client = client or miniflux.Client(
            base_url, api_key=api_key, timeout=self.TIMEOUT_SECONDS, user_agent="news-displayer"
        )

    def latest(self, limit: int) -> list[Article]:
        try:
            result = self._client.get_entries(order="published_at", direction="desc", limit=limit)
        except (miniflux.ClientError, requests.RequestException) as exc:
            raise SourceUnavailable(type(exc).__name__) from exc
        return [self._to_article(entry) for entry in result.get("entries", [])]

    def get(self, article_id: int) -> Article:
        try:
            entry = self._client.get_entry(article_id)
        except miniflux.ResourceNotFound as exc:
            raise ArticleNotFound(article_id) from exc
        except (miniflux.ClientError, requests.RequestException) as exc:
            raise SourceUnavailable(type(exc).__name__) from exc
        return self._to_article(entry)

    @staticmethod
    def _to_article(entry: dict) -> Article:
        return Article(
            id=entry["id"],
            title=entry.get("title", ""),
            url=_safe_url(entry.get("url", "")),
            feed_title=(entry.get("feed") or {}).get("title", ""),
            published_at=datetime.fromisoformat(entry["published_at"]),
            content=entry.get("content") or "",
        )


def _safe_url(url: str) -> str:
    """Feed URLs end up in href attributes, so drop anything that isn't http(s) (e.g. javascript:)."""
    return url if url.lower().startswith(("http://", "https://")) else ""


def get_source() -> NewsSource:
    return MinifluxSource(settings.MINIFLUX_URL, settings.MINIFLUX_API_KEY)
