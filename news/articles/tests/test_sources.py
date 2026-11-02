from datetime import datetime, timezone
from unittest.mock import Mock

import miniflux
import pytest
import requests

from articles.sources import ArticleNotFound, MinifluxSource, SourceUnavailable


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


def test_latest_asks_for_newest_first_and_maps_entries():
    client = Mock()
    client.get_entries.return_value = {"total": 1, "entries": [_entry()]}

    articles = MinifluxSource("", "", client=client).latest(5)

    client.get_entries.assert_called_once_with(order="published_at", direction="desc", limit=5)
    [article] = articles
    assert article.id == 7
    assert article.title == "Neue Brücke eröffnet"
    assert article.feed_title == "Stadtnachrichten"
    assert article.published_at == datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)
    assert article.content == "<p>Die Brücke ist offen.</p>"


def test_get_maps_single_entry():
    client = Mock()
    client.get_entry.return_value = _entry()

    assert MinifluxSource("", "", client=client).get(7).id == 7
    client.get_entry.assert_called_once_with(7)


def test_non_http_urls_are_dropped():
    client = Mock()
    client.get_entry.return_value = _entry(url="javascript:alert(1)")

    assert MinifluxSource("", "", client=client).get(7).url == ""


def test_missing_entry_raises_not_found():
    client = Mock()
    client.get_entry.side_effect = _error(miniflux.ResourceNotFound, 404)

    with pytest.raises(ArticleNotFound):
        MinifluxSource("", "", client=client).get(7)


@pytest.mark.parametrize(
    "error",
    [_error(miniflux.AccessUnauthorized, 401), _error(miniflux.ServerError, 500), requests.ConnectionError()],
)
def test_backend_failures_raise_unavailable(error):
    client = Mock()
    client.get_entries.side_effect = error
    client.get_entry.side_effect = error
    source = MinifluxSource("", "", client=client)

    with pytest.raises(SourceUnavailable):
        source.latest(5)
    with pytest.raises(SourceUnavailable):
        source.get(7)
