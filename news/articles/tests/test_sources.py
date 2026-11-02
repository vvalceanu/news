from datetime import datetime, timezone
from unittest.mock import Mock

import miniflux
import pytest
import requests

from articles.sources import MinifluxSource, SourceUnavailable


def _entry(**overrides):
    entry = {
        "id": 7,
        "title": "Neue Brücke eröffnet",
        "url": "https://example.com/bruecke",
        "published_at": "2026-11-01T08:30:00Z",
        "content": "<p>Die Brücke ist offen.</p>",
        "feed": {"title": "Stadtnachrichten"},
    }
    entry.update(overrides)
    return entry


def _error(cls, status):
    response = Mock(status_code=status)
    response.json.return_value = {"error_message": "boom"}
    return cls(response)


def test_entries_published_after_asks_for_oldest_first():
    client = Mock()
    client.get_entries.return_value = {"total": 1, "entries": [_entry()]}
    cutoff = datetime(2026, 11, 1, tzinfo=timezone.utc)

    [article] = MinifluxSource("", "", client=client).entries_published_after(cutoff, limit=100, offset=200)

    client.get_entries.assert_called_once_with(
        order="published_at", direction="asc", limit=100, offset=200, published_after=int(cutoff.timestamp())
    )
    assert article.id == 7


CUTOFF = datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_entries_are_mapped_to_articles():
    client = Mock()
    client.get_entries.return_value = {"total": 1, "entries": [_entry()]}

    [article] = MinifluxSource("", "", client=client).entries_published_after(CUTOFF, limit=100)

    assert article.id == 7
    assert article.title == "Neue Brücke eröffnet"
    assert article.url == "https://example.com/bruecke"
    assert article.feed_title == "Stadtnachrichten"
    assert article.published_at == datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)
    assert article.content == "<p>Die Brücke ist offen.</p>"


def test_non_http_urls_are_dropped():
    client = Mock()
    client.get_entries.return_value = {"total": 1, "entries": [_entry(url="javascript:alert(1)")]}

    [article] = MinifluxSource("", "", client=client).entries_published_after(CUTOFF, limit=100)

    assert article.url == ""


@pytest.mark.parametrize(
    "error",
    [_error(miniflux.AccessUnauthorized, 401), _error(miniflux.ServerError, 500), requests.ConnectionError()],
)
def test_backend_failures_raise_unavailable(error):
    client = Mock()
    client.get_entries.side_effect = error

    with pytest.raises(SourceUnavailable):
        MinifluxSource("", "", client=client).entries_published_after(CUTOFF, limit=100)
