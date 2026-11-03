import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from django.core.management import CommandError, call_command

from articles import deep_dive, tasks
from articles.models import Article
from articles.tests.fakes import NOW

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
HTML = "<script>alert(1)</script><h2>Neue Brücke</h2><p>Die <a href='https://x.test'>Brücke</a> ist offen.<img src='x.png'></p>"


def _reply(german="Die Brücke ist offen.", english="The bridge is open.", stop_reason="end_turn", text=None):
    if text is None:
        text = json.dumps({"german": german, "english": english})
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=2_000, output_tokens=3_000),  # $0.004 + $0.03
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


CALL_COST = Decimal("0.034")


class FakeAnthropic:
    """Answers each request via `respond(level)`, which returns a reply or raises."""

    def __init__(self, respond=lambda level: _reply(f"Zusammenfassung {level}", f"Summary {level}")):
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)
        self._respond = respond

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        level = "A2" if "Level A2" in kwargs["system"] else "B1"
        return self._respond(level)


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    monkeypatch.setattr(tasks.timezone, "now", lambda: NOW)


@pytest.fixture
def queued_tasks(monkeypatch):
    queued = []
    monkeypatch.setattr(tasks, "async_task", lambda func, *args: queued.append((func, args)))
    return queued


def _article(miniflux_id=1, content=HTML, **fields):
    return Article.objects.create(
        miniflux_id=miniflux_id, title="Neue Brücke eröffnet", published_at=NOW - timedelta(hours=1), content=content, **fields
    )


def _queued(miniflux_id=1, **fields):
    return _article(miniflux_id, deep_dive_status="queued", deep_dive_requested_at=NOW, **fields)


def _reload(article):
    article.refresh_from_db()
    return article


def _bad_request():
    return anthropic.BadRequestError("bad", response=httpx2.Response(400, request=REQUEST), body=None)


# HTML to Markdown


def test_article_markdown_keeps_text_and_drops_scripts_links_and_images():
    assert deep_dive.article_markdown(HTML) == "## Neue Brücke\n\nDie Brücke ist offen."


def test_article_markdown_of_empty_body_is_empty():
    assert deep_dive.article_markdown("") == ""


# The calls


@pytest.mark.django_db
def test_deep_dive_writes_both_levels_with_one_sonnet_call_each():
    article = _queued()
    client = FakeAnthropic()

    assert tasks.deep_dive(article.pk, client) is True

    article = _reload(article)
    assert article.deep_dive_status == "done"
    assert (article.summary_b1, article.summary_b1_en) == ("Zusammenfassung B1", "Summary B1")
    assert (article.summary_a2, article.summary_a2_en) == ("Zusammenfassung A2", "Summary A2")
    assert article.deep_dive_cost_usd == 2 * CALL_COST
    b1, a2 = client.requests
    for request in (b1, a2):
        assert request["model"] == "claude-sonnet-5-5"
        assert request["thinking"] == {"type": "adaptive"}
        assert request["output_config"]["effort"] == "medium"
        assert request["output_config"]["format"]["schema"]["required"] == ["german", "english"]
        assert request["messages"][0]["content"] == "<article>\n# Neue Brücke eröffnet\n\n## Neue Brücke\n\nDie Brücke ist offen.\n</article>"
    assert "Level B1" in b1["system"] and "Level A2" in a2["system"]
    assert "Swiss Standard German" in b1["system"] and "never ß" in b1["system"]


@pytest.mark.django_db
def test_german_summary_never_keeps_an_eszett():
    article = _queued()

    tasks.deep_dive(article.pk, FakeAnthropic(lambda level: _reply("Die Strasse ist groß. GROẞ!", "The street is big.")))

    assert _reload(article).summary_b1 == "Die Strasse ist gross. GROSS!"


@pytest.mark.parametrize(
    "reply",
    [
        lambda: _reply(stop_reason="refusal"),
        lambda: _reply(stop_reason="max_tokens"),
        lambda: _reply(text="not json"),
        lambda: _reply(german=" "),
    ],
)
@pytest.mark.django_db
def test_a_failed_level_is_stored_empty_and_still_counts_its_cost(reply):
    article = _queued()

    def respond(level):
        return reply() if level == "B1" else _reply("A2 Text", "A2 text")

    tasks.deep_dive(article.pk, FakeAnthropic(respond))

    article = _reload(article)
    assert (article.summary_b1, article.summary_b1_en) == ("", "")
    assert (article.summary_a2, article.summary_a2_en) == ("A2 Text", "A2 text")
    assert article.deep_dive_status == "done"
    assert article.deep_dive_cost_usd == 2 * CALL_COST


@pytest.mark.parametrize(
    "error",
    [_bad_request, lambda: anthropic.APIConnectionError(request=REQUEST), lambda: anthropic.APITimeoutError(REQUEST)],
)
@pytest.mark.django_db
def test_an_api_error_leaves_that_level_empty_and_the_other_still_runs(error):
    article = _queued()

    def respond(level):
        if level == "A2":
            raise error()
        return _reply("B1 Text", "B1 text")

    tasks.deep_dive(article.pk, FakeAnthropic(respond))

    article = _reload(article)
    assert article.summary_b1 == "B1 Text"
    assert (article.summary_a2, article.summary_a2_en) == ("", "")
    assert article.deep_dive_status == "done"
    assert article.deep_dive_cost_usd == CALL_COST


@pytest.mark.django_db
def test_an_unexpected_crash_still_marks_the_article_done_so_it_never_reruns():
    article = _queued()

    def respond(level):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        tasks.deep_dive(article.pk, FakeAnthropic(respond))

    assert _reload(article).deep_dive_status == "done"


@pytest.mark.django_db
def test_empty_body_makes_no_calls():
    article = _queued(content="<p> </p>")
    client = FakeAnthropic()

    tasks.deep_dive(article.pk, client)

    assert client.requests == []
    assert _reload(article).deep_dive_status == "done"


@pytest.mark.parametrize("status", ["", "running", "done"])
@pytest.mark.django_db
def test_runs_only_queued_articles(status):
    article = _article(deep_dive_status=status)
    client = FakeAnthropic()

    assert tasks.deep_dive(article.pk, client) is False

    assert client.requests == []
    assert _reload(article).deep_dive_status == status


@pytest.mark.django_db
def test_missing_api_key_puts_the_article_back(settings):
    settings.ANTHROPIC_API_KEY = ""
    article = _queued()

    with pytest.raises(Exception, match="ANTHROPIC_API_KEY"):
        tasks.deep_dive(article.pk)

    article = _reload(article)
    assert (article.deep_dive_status, article.deep_dive_requested_at) == ("", None)


# Daily cap


@pytest.mark.django_db
def test_worker_skips_and_puts_back_an_article_once_todays_cap_is_spent(settings):
    settings.DEEP_DIVE_DAILY_CAP_USD = Decimal("1")
    _article(1, deep_dive_status="done", deep_dive_requested_at=NOW, deep_dive_cost_usd=Decimal("1"))
    article = _queued(2)
    client = FakeAnthropic()

    assert tasks.deep_dive(article.pk, client) is False

    assert client.requests == []
    assert _reload(article).deep_dive_status == ""


@pytest.mark.django_db
def test_spending_from_before_local_midnight_does_not_count(settings):
    # NOW is 13:00 in Berlin; 14 hours earlier is yesterday there.
    _article(1, deep_dive_status="done", deep_dive_requested_at=NOW - timedelta(hours=14), deep_dive_cost_usd=Decimal("5"))
    _article(2, deep_dive_status="done", deep_dive_requested_at=NOW - timedelta(hours=1), deep_dive_cost_usd=Decimal("0.25"))

    assert tasks.spent_today() == Decimal("0.25")


@pytest.mark.django_db
def test_request_queues_a_new_article(queued_tasks):
    article = _article()

    assert tasks.request_deep_dive(article.pk) is tasks.DeepDiveRequest.QUEUED

    article = _reload(article)
    assert (article.deep_dive_status, article.deep_dive_requested_at) == ("queued", NOW)
    assert queued_tasks == [("articles.tasks.deep_dive", (article.pk,))]


@pytest.mark.parametrize("status", ["queued", "running", "done"])
@pytest.mark.django_db
def test_request_never_queues_an_article_twice(queued_tasks, status):
    article = _article(deep_dive_status=status)

    assert tasks.request_deep_dive(article.pk) is tasks.DeepDiveRequest.ALREADY_REQUESTED

    assert queued_tasks == []


@pytest.mark.django_db
def test_request_counts_in_flight_deep_dives_against_the_cap(settings, queued_tasks):
    settings.DEEP_DIVE_DAILY_CAP_USD = Decimal("0.25")
    _article(1, deep_dive_status="done", deep_dive_requested_at=NOW, deep_dive_cost_usd=Decimal("0.05"))
    _queued(2)
    _article(3, deep_dive_status="running", deep_dive_requested_at=NOW)
    article = _article(4)

    # 0.05 spent + 2 in flight x 0.10 = 0.25
    assert tasks.request_deep_dive(article.pk) is tasks.DeepDiveRequest.OVER_DAILY_CAP

    assert _reload(article).deep_dive_status == ""
    assert queued_tasks == []


# The POST


@pytest.mark.django_db
def test_post_queues_and_redirects_to_the_article(client, queued_tasks):
    article = _article()

    response = client.post(f"/articles/{article.pk}/deep-dive/")

    assert response.status_code == 302
    assert response["Location"] == f"/articles/{article.pk}/"
    assert _reload(article).deep_dive_status == "queued"
    assert len(queued_tasks) == 1


@pytest.mark.django_db
def test_get_never_queues(client, queued_tasks):
    article = _article()

    assert client.get(f"/articles/{article.pk}/deep-dive/").status_code == 405

    assert _reload(article).deep_dive_status == ""
    assert queued_tasks == []


@pytest.mark.django_db
def test_post_needs_a_csrf_token(queued_tasks):
    from django.test import Client

    article = _article()

    assert Client(enforce_csrf_checks=True).post(f"/articles/{article.pk}/deep-dive/").status_code == 403
    assert queued_tasks == []


@pytest.mark.django_db
def test_post_to_unknown_article_is_404(client, queued_tasks):
    assert client.post("/articles/999/deep-dive/").status_code == 404


@pytest.mark.django_db
def test_post_over_the_daily_cap_says_so(client, settings, queued_tasks):
    settings.DEEP_DIVE_DAILY_CAP_USD = Decimal("0")
    article = _article()

    response = client.post(f"/articles/{article.pk}/deep-dive/")

    assert response.status_code == 503
    assert "Tageslimit" in response.content.decode()
    assert queued_tasks == []


@pytest.mark.django_db
def test_post_is_rate_limited_per_client_ip(client, monkeypatch, queued_tasks):
    from articles import views

    monkeypatch.setattr(views, "DEEP_DIVE_RATE", "2/h")
    articles = [_article(i) for i in range(1, 5)]

    def post(article, ip):
        return client.post(f"/articles/{article.pk}/deep-dive/", HTTP_X_REAL_IP=ip).status_code

    assert [post(a, "203.0.113.7") for a in articles[:3]] == [302, 302, 429]
    assert post(articles[3], "203.0.113.8") == 302
    assert _reload(articles[2]).deep_dive_status == ""


# The article page


@pytest.mark.django_db
def test_page_offers_the_button_as_a_post_form(client):
    article = _article()
    body = client.get(f"/articles/{article.pk}/").content.decode()
    assert f'<form method="post" action="/articles/{article.pk}/deep-dive/">' in body
    assert "csrfmiddlewaretoken" in body


@pytest.mark.parametrize("status", ["queued", "running"])
@pytest.mark.django_db
def test_page_reloads_while_the_deep_dive_runs(client, status):
    article = _article(deep_dive_status=status)
    body = client.get(f"/articles/{article.pk}/").content.decode()
    assert 'http-equiv="refresh"' in body
    assert "<form" not in body


@pytest.mark.django_db
def test_page_shows_summaries_escaped_with_paragraphs(client):
    article = _article(
        deep_dive_status="done",
        summary_b1="Erster Absatz.\n\n<b>Zweiter</b> Absatz.",
        summary_b1_en="First paragraph.",
        summary_a2="",
    )
    body = client.get(f"/articles/{article.pk}/").content.decode()
    assert "<p>Erster Absatz.</p>" in body
    assert "&lt;b&gt;Zweiter&lt;/b&gt; Absatz." in body
    assert "First paragraph." in body
    assert "Keine Zusammenfassung." in body  # A2 failed
    assert body.index("<h2>A2</h2>") < body.index("<h2>B1</h2>")  # easier level first
    assert 'http-equiv="refresh"' not in body


# Management command


@pytest.mark.django_db
def test_command_runs_the_deep_dive_in_process(monkeypatch):
    article = _article()
    monkeypatch.setattr(tasks, "anthropic_client", lambda **kwargs: FakeAnthropic())
    out = StringIO()

    call_command("deep_dive", article.pk, stdout=out)

    assert _reload(article).summary_b1 == "Zusammenfassung B1"
    assert "Zusammenfassung A2" in out.getvalue()


@pytest.mark.django_db
def test_command_refuses_to_redo_without_force(monkeypatch):
    article = _article(deep_dive_status="done", summary_b1="alt")
    monkeypatch.setattr(tasks, "anthropic_client", lambda **kwargs: FakeAnthropic())

    with pytest.raises(CommandError, match="--force"):
        call_command("deep_dive", article.pk)

    call_command("deep_dive", article.pk, "--force", stdout=StringIO())
    assert _reload(article).summary_b1 == "Zusammenfassung B1"


@pytest.mark.django_db
def test_command_without_api_key_fails_cleanly(settings):
    settings.ANTHROPIC_API_KEY = ""
    article = _article()

    with pytest.raises(CommandError, match="ANTHROPIC_API_KEY"):
        call_command("deep_dive", article.pk)
