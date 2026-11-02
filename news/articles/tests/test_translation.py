from datetime import timedelta
from io import StringIO
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command

from articles import tasks
from articles.models import Article
from articles.sources import SourceUnavailable
from articles.tests.fakes import NOW

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _reply(text, stop_reason="end_turn"):
    # Haiku 5.5 thinks by default, so a real response can start with an (empty) thinking block.
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


def _bad_request():
    return anthropic.BadRequestError("bad", response=httpx2.Response(400, request=REQUEST), body=None)


class FakeAnthropic:
    """Answers each request via `respond(title)`, which returns a reply or raises."""

    def __init__(self, respond=lambda title: _reply(f"EN {title}")):
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)
        self._respond = respond

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._respond(kwargs["messages"][0]["content"])

    def titles_sent(self):
        return [r["messages"][0]["content"] for r in self.requests]


def _article(miniflux_id, title=None, hours_ago=1, **fields):
    return Article.objects.create(
        miniflux_id=miniflux_id,
        title=title or f"Titel {miniflux_id}",
        published_at=NOW - timedelta(hours=hours_ago),
        **fields,
    )


def _reload(article):
    article.refresh_from_db()
    return article


@pytest.mark.django_db
def test_translates_untranslated_titles_newest_first_up_to_the_limit():
    old, mid, new = _article(1, hours_ago=3), _article(2, hours_ago=2), _article(3, hours_ago=1)

    assert tasks.translate_titles(FakeAnthropic(), limit=2) == 2

    assert _reload(new).title_en == "EN <headline>Titel 3</headline>"
    assert _reload(mid).title_en == "EN <headline>Titel 2</headline>"
    assert _reload(old).title_en == ""


@pytest.mark.django_db
def test_one_haiku_call_per_title_at_low_effort():
    _article(1, title="Neue Brücke eröffnet")
    client = FakeAnthropic()

    tasks.translate_titles(client)

    [request] = client.requests
    assert request["model"] == "claude-haiku-5-5"
    assert request["output_config"] == {"effort": "low"}
    assert request["messages"] == [{"role": "user", "content": "<headline>Neue Brücke eröffnet</headline>"}]


@pytest.mark.django_db
def test_translated_and_already_attempted_titles_are_skipped():
    _article(1, title_en="Done")
    _article(2, title_en_attempts=1)
    client = FakeAnthropic()

    assert tasks.translate_titles(client) == 0
    assert client.requests == []


@pytest.mark.django_db
def test_a_failed_title_is_not_retried_and_does_not_stop_the_others():
    bad, good = _article(1, title="Kaputt", hours_ago=1), _article(2, hours_ago=2)

    def respond(content):
        if "Kaputt" in content:
            raise _bad_request()
        return _reply("Fine")

    client = FakeAnthropic(respond)
    assert tasks.translate_titles(client) == 1
    assert _reload(good).title_en == "Fine"
    assert (_reload(bad).title_en, bad.title_en_attempts) == ("", 1)

    client.requests.clear()
    tasks.translate_titles(client)
    assert client.requests == []


@pytest.mark.django_db
@pytest.mark.parametrize("reply", [_reply("Something", stop_reason="refusal"), _reply("   ")])
def test_refusal_or_empty_answer_counts_as_a_failure(reply):
    article = _article(1)

    assert tasks.translate_titles(FakeAnthropic(lambda content: reply)) == 0

    assert (_reload(article).title_en, article.title_en_attempts) == ("", 1)


@pytest.mark.django_db
def test_an_outage_stops_the_run_and_leaves_titles_for_the_next_run():
    first, second = _article(1, hours_ago=1), _article(2, hours_ago=2)

    def respond(content):
        raise anthropic.APIConnectionError(request=REQUEST)

    client = FakeAnthropic(respond)
    assert tasks.translate_titles(client) == 0

    assert len(client.requests) == 1
    assert _reload(first).title_en_attempts == 0
    assert _reload(second).title_en_attempts == 0


@pytest.mark.django_db
def test_missing_api_key_fails_before_touching_any_article(settings):
    settings.ANTHROPIC_API_KEY = ""
    article = _article(1)

    with pytest.raises(ImproperlyConfigured):
        tasks.translate_titles()

    assert _reload(article).title_en_attempts == 0


@pytest.mark.django_db
def test_scheduled_sweep_queues_title_translation(monkeypatch):
    queued = []
    monkeypatch.setattr(tasks, "sweep", lambda: 3)
    monkeypatch.setattr(tasks, "async_task", lambda func: queued.append(func))

    tasks.run_sweep()

    assert queued == ["articles.tasks.translate_titles"]


@pytest.mark.django_db
def test_scheduled_sweep_queues_translation_even_when_miniflux_is_down(monkeypatch):
    queued = []

    def down():
        raise SourceUnavailable("ConnectTimeout")

    monkeypatch.setattr(tasks, "sweep", down)
    monkeypatch.setattr(tasks, "async_task", lambda func: queued.append(func))

    with pytest.raises(SourceUnavailable):
        tasks.run_sweep()
    assert queued == ["articles.tasks.translate_titles"]


@pytest.mark.django_db
def test_translate_titles_command(monkeypatch):
    monkeypatch.setattr(tasks, "anthropic_client", lambda: FakeAnthropic())
    _article(1)
    out = StringIO()

    call_command("translate_titles", stdout=out)

    assert "Translated 1 titles." in out.getvalue()


@pytest.mark.django_db
def test_translate_titles_command_without_api_key_is_one_line(settings):
    settings.ANTHROPIC_API_KEY = ""

    with pytest.raises(CommandError, match="ANTHROPIC_API_KEY"):
        call_command("translate_titles")
