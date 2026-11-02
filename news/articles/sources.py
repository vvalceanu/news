"""Where articles come from.

Only the sweep (`articles.tasks`) talks to `NewsSource`; the views read our own `Article`
table. Miniflux can be swapped for another feed backend without touching anything else.
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


class SourceUnavailable(Exception):
    """The backend could not be reached or answered with an error."""


class NewsSource(Protocol):
    def entries_published_after(self, published_after: datetime, limit: int, offset: int = 0) -> list[Article]:
        """Entries published after `published_after`, oldest first, `limit` of them starting at `offset`."""
        ...


class MinifluxSource:
    TIMEOUT_SECONDS = 10.0

    def __init__(self, base_url: str, api_key: str, client=None):
        self._client = client or miniflux.Client(
            base_url, api_key=api_key, timeout=self.TIMEOUT_SECONDS, user_agent="news-displayer"
        )

    def entries_published_after(self, published_after: datetime, limit: int, offset: int = 0) -> list[Article]:
        params = {
            "order": "published_at",
            "direction": "asc",
            "limit": limit,
            "offset": offset,
            "published_after": int(published_after.timestamp()),
        }
        try:
            result = self._client.get_entries(**params)
        except (miniflux.ClientError, requests.RequestException) as exc:
            raise SourceUnavailable(type(exc).__name__) from exc
        return [self._to_article(entry) for entry in result.get("entries", [])]

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
