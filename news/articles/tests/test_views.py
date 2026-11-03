from datetime import datetime, timedelta, timezone

import pytest

from articles import views
from articles.models import Article
from articles.sources import SourceUnavailable
from articles.tests.fakes import NOW, FakeMiniflux, entry

PUBLISHED = datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)


def _article(miniflux_id=7, **overrides):
    fields = dict(
        miniflux_id=miniflux_id,
        title="Neue Brücke eröffnet",
        url="https://example.com/bruecke",
        feed_title="Stadtnachrichten",
        published_at=PUBLISHED,
        content="<script>alert(1)</script><p>Die Brücke ist offen.</p>",
    )
    fields.update(overrides)
    return Article.objects.create(**fields)


@pytest.mark.django_db
def test_index_lists_stored_articles_linking_to_detail(client):
    article = _article()
    body = client.get("/").content.decode()
    assert "Neue Brücke eröffnet" in body
    assert f'href="/articles/{article.pk}/"' in body
    assert "Stadtnachrichten" in body


@pytest.mark.django_db
def test_index_shows_newest_first(client):
    _article(1, title="Älter", published_at=PUBLISHED - timedelta(hours=1))
    _article(2, title="Neuer", published_at=PUBLISHED)
    body = client.get("/").content.decode()
    assert body.index("Neuer") < body.index("Älter")


@pytest.mark.django_db
def test_index_shows_at_most_the_latest_limit(client, monkeypatch):
    monkeypatch.setattr(views, "LATEST_LIMIT", 1)
    _article(1, title="Älter", published_at=PUBLISHED - timedelta(hours=1))
    _article(2, title="Neuer", published_at=PUBLISHED)
    body = client.get("/").content.decode()
    assert "Neuer" in body
    assert "Älter" not in body


@pytest.mark.django_db
def test_index_handles_empty_list(client):
    assert "No articles." in client.get("/").content.decode()


def test_index_rejects_post(client):
    assert client.post("/").status_code == 405


@pytest.mark.django_db
def test_detail_shows_the_body_as_sanitized_text_not_html_source(client):
    article = _article(
        content=(
            "<script>alert(1)</script><h2>Kopf</h2><p>Die <a href='https://x.test'>Brücke</a> ist <b>offen</b>.</p>"
            "<p>[klick](javascript:alert(1)) &lt;img src=x onerror=alert(1)&gt;</p>"
        )
    )
    response = client.get(f"/articles/{article.pk}/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "<h2>Kopf</h2>" in body
    assert "<p>Die Brücke ist <strong>offen</strong>.</p>" in body
    for unsafe in ("alert(1)</script>", "javascript:", "<img", "https://x.test", "<pre>"):
        assert unsafe not in body


@pytest.mark.django_db
def test_detail_unknown_article_is_404(client):
    assert client.get("/articles/999/").status_code == 404


@pytest.fixture
def use_source(monkeypatch):
    def install(source):
        monkeypatch.setattr(views, "get_source", lambda: source)
        return source

    return install


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/", "/articles/1/"])
def test_stored_article_pages_never_call_miniflux(client, use_source, path):
    source = use_source(FakeMiniflux())
    _article(1)
    assert client.get(path).status_code == 200
    assert source.calls == []


@pytest.mark.django_db
def test_index_links_to_pending(client):
    assert 'href="/pending/"' in client.get("/").content.decode()


@pytest.mark.django_db
def test_pending_lists_only_miniflux_entries_we_do_not_have(client, use_source, monkeypatch):
    monkeypatch.setattr("articles.tasks.timezone.now", lambda: NOW)
    _article(1, title="Schon gespeichert")
    use_source(FakeMiniflux([entry(1, title="Schon gespeichert"), entry(2, title="Noch nicht gespeichert")]))

    response = client.get("/pending/")
    body = response.content.decode()

    assert response.status_code == 200
    assert "Noch nicht gespeichert" in body
    assert "Schon gespeichert" not in body
    assert 'href="https://example.com/2"' in body
    assert 'href="/"' in body


@pytest.mark.django_db
def test_pending_handles_nothing_new(client, use_source):
    use_source(FakeMiniflux())
    assert "Keine neuen Artikel." in client.get("/pending/").content.decode()


@pytest.mark.django_db
def test_pending_when_miniflux_is_down_returns_503(client, use_source):
    use_source(FakeMiniflux(error=SourceUnavailable("ConnectionError")))
    response = client.get("/pending/")
    assert response.status_code == 503
    assert "Miniflux ist gerade nicht erreichbar." in response.content.decode()


def test_pending_rejects_post(client):
    assert client.post("/pending/").status_code == 405


@pytest.mark.django_db
def test_index_shows_total_count_of_stored_articles_beyond_the_list_limit(client, monkeypatch):
    monkeypatch.setattr(views, "LATEST_LIMIT", 1)
    for i in range(3):
        _article(i)
    assert "3 articles stored" in client.get("/").content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["index", "detail"])
def test_pages_show_the_english_title_when_there_is_one(client, page):
    article = _article(title_en="New bridge opened")
    path = "/" if page == "index" else f"/articles/{article.pk}/"
    assert "New bridge opened" in client.get(path).content.decode()


@pytest.mark.django_db
def test_index_shows_relative_publish_dates(client, monkeypatch):
    monkeypatch.setattr("django.utils.timezone.now", lambda: datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc))
    _article(1, title="Heute", published_at=datetime(2026, 11, 2, 7, 15, tzinfo=timezone.utc))
    _article(2, title="Gestern", published_at=PUBLISHED)
    body = client.get("/").content.decode()
    assert "Heute, 08:15" in body
    assert "Gestern, 09:30" in body
