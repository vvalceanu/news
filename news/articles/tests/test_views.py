from datetime import datetime, timezone

import pytest

from articles import views
from articles.sources import Article, ArticleNotFound, SourceUnavailable

ARTICLE = Article(
    id=7,
    title="Neue Brücke eröffnet",
    url="https://example.com/bruecke",
    feed_title="Stadtnachrichten",
    published_at=datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc),
    content="<script>alert(1)</script><p>Die Brücke ist offen.</p>",
)


class FakeSource:
    def __init__(self, articles=(), error=None):
        self.articles = {a.id: a for a in articles}
        self.error = error

    def latest(self, limit):
        if self.error:
            raise self.error
        return list(self.articles.values())[:limit]

    def get(self, article_id):
        if self.error:
            raise self.error
        try:
            return self.articles[article_id]
        except KeyError:
            raise ArticleNotFound(article_id)


@pytest.fixture
def use_source(monkeypatch):
    def install(source):
        monkeypatch.setattr(views, "get_source", lambda: source)

    return install


def test_index_lists_articles_linking_to_detail(client, use_source):
    use_source(FakeSource([ARTICLE]))
    body = client.get("/").content.decode()
    assert "Neue Brücke eröffnet" in body
    assert 'href="/articles/7/"' in body
    assert "Stadtnachrichten" in body


def test_index_handles_empty_list(client, use_source):
    use_source(FakeSource())
    assert "Keine Artikel." in client.get("/").content.decode()


def test_index_rejects_post(client):
    assert client.post("/").status_code == 405


def test_detail_shows_raw_body_escaped(client, use_source):
    use_source(FakeSource([ARTICLE]))
    response = client.get("/articles/7/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;&lt;p&gt;Die Brücke ist offen.&lt;/p&gt;" in body
    assert "<script>alert(1)</script>" not in body


def test_detail_unknown_article_is_404(client, use_source):
    use_source(FakeSource())
    assert client.get("/articles/999/").status_code == 404


@pytest.mark.parametrize("path", ["/", "/articles/7/"])
def test_source_unavailable_returns_503(client, use_source, path):
    use_source(FakeSource(error=SourceUnavailable("ConnectionError")))
    response = client.get(path)
    assert response.status_code == 503
    assert "Nicht verfügbar" in response.content.decode()
