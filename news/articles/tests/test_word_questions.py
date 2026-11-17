import json
import re
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from django.test import Client

from articles import tasks, views, word_questions
from articles.models import Article, WordQuestion
from articles.tests.fakes import NOW
from articles.tests.test_words import _done_article


def _w(text, ws=" ", lemma=None, tag="", verb=None):
    return {"text": text, "ws": ws, "lemma": text.lower() if lemma is None else lemma, "verb": verb,
            "known": True, "pos": "", "tag": tag, "dep": "", "morph": ""}


# "Die Firma teilte die Zahlen mit. Es gibt 373 Stellen.\n\nNeu"
WORDS = [
    _w("Die"), _w("Firma"), _w("teilte", lemma="mitteilen"), _w("die"), _w("Zahlen"),
    _w("mit", ws="", lemma="mitteilen", verb=2), _w(".", lemma="", tag="$."),
    _w("Es"), _w("gibt"), _w("373", lemma=""), _w("Stellen", ws=""), _w(".", ws="", lemma="", tag="$."),
    _w("\n\n", ws="", lemma=""), _w("Neu", ws=""),
]
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _reply(text="It means: announced.", stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=100_000, output_tokens=200_000),  # $0.01 + $0.10
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


CALL_COST = Decimal("0.11")


class FakeAnthropic:
    def __init__(self, respond=lambda: _reply()):
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)
        self._respond = respond

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._respond()


@pytest.fixture
def haiku(monkeypatch):
    fake = FakeAnthropic()
    monkeypatch.setattr(word_questions, "anthropic_client", lambda **kwargs: fake)
    return fake


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    monkeypatch.setattr(tasks.timezone, "now", lambda: NOW)  # django.utils.timezone, for every module


def _article(**fields):
    return _done_article(words_b1=WORDS, **fields)


def _ask(client, article, index, level="b1", ip="203.0.113.7"):
    return client.post(f"/articles/{article.pk}/words/{level}/{index}/ask/", HTTP_X_REAL_IP=ip)


# The question


def test_the_sentence_is_cut_at_sentence_ends_and_blank_lines():
    assert word_questions.sentence(WORDS, 4) == "Die Firma teilte die Zahlen mit."
    assert word_questions.sentence(WORDS, 8) == "Es gibt 373 Stellen."
    assert word_questions.sentence(WORDS, 13) == "Neu"


def test_a_separable_verb_is_asked_about_whole():
    assert word_questions.asked_word(WORDS, 2) == "teilte … mit"
    assert word_questions.asked_word(WORDS, 1) == "Firma"


def test_ask_sends_the_users_question_and_computes_the_cost():
    fake = FakeAnthropic()
    answer = word_questions.ask(fake, "Die Firma teilte die Zahlen mit.", "teilte … mit")

    assert answer == word_questions.Answer("It means: announced.", CALL_COST)
    [request] = fake.requests
    assert request["model"] == "claude-haiku-5-5"
    assert request["messages"] == [{"role": "user", "content": (
        'In the sentence "Die Firma teilte die Zahlen mit.", what does "teilte … mit" mean? '
        "Answer in English. If there are several possible interpretations, list them all."
    )}]


@pytest.mark.parametrize("reply", [_reply(stop_reason="max_tokens"), _reply(text="  ")])
def test_ask_fails_without_a_usable_answer_but_reports_the_cost(reply):
    with pytest.raises(word_questions.AskFailed) as failed:
        word_questions.ask(FakeAnthropic(lambda: reply), "Satz.", "Satz")
    assert failed.value.cost_usd == CALL_COST


# The endpoint (displayer) and the task (worker)


@pytest.fixture
def queued(monkeypatch):
    """Questions the displayer queued for the worker, by question id."""
    calls = []
    monkeypatch.setattr(word_questions, "async_task", lambda name, *args: calls.append((name, *args)))
    return calls


def _work(queued):
    """Run what the displayer queued, as the worker would."""
    while queued:
        name, question_id = queued.pop(0)
        assert name == "articles.word_questions.answer_question"
        word_questions.answer_question(question_id)


def _poll(client, article, index, level="b1"):
    return client.get(f"/articles/{article.pk}/words/{level}/{index}/ask/")


@pytest.mark.django_db
def test_asking_queues_the_question_and_the_worker_answers_from_our_own_text(client, haiku, queued):
    article = _article()

    response = _ask(client, article, 4)

    assert response.status_code == 202
    assert response.json() == {"status": "waiting"}
    assert haiku.requests == []  # the displayer never calls Claude
    assert _poll(client, article, 4).json() == {"status": "waiting"}

    _work(queued)

    [request] = haiku.requests
    assert '"Die Firma teilte die Zahlen mit."' in request["messages"][0]["content"]
    assert _poll(client, article, 4).json() == {"status": "done", "answer": "It means: announced."}
    question = WordQuestion.objects.get()
    assert (question.level, question.index, question.word, question.status, question.cost_usd) == (
        "b1", 4, "Zahlen", "done", CALL_COST)
    assert question.sentence == "Die Firma teilte die Zahlen mit."


@pytest.mark.django_db
def test_a_saved_answer_is_never_asked_again(client, haiku, queued):
    article = _article()
    _ask(client, article, 4)
    _work(queued)

    response = _ask(client, article, 4, ip="203.0.113.8")

    assert response.status_code == 200
    assert response.json() == {"status": "done", "answer": "It means: announced."}
    assert queued == []
    assert len(haiku.requests) == 1


@pytest.mark.django_db
def test_a_waiting_question_is_queued_once(client, haiku, queued):
    article = _article()
    _ask(client, article, 4)
    _ask(client, article, 4, ip="203.0.113.8")

    assert len(queued) == 1
    _work(queued)
    assert len(haiku.requests) == 1


@pytest.mark.django_db
def test_a_question_the_worker_never_picked_up_can_be_asked_again(client, haiku, queued):
    article = _article()
    _ask(client, article, 4)
    queued.clear()  # lost, e.g. the worker restarted
    WordQuestion.objects.update(asked_at=NOW - word_questions.STALE_AFTER - timedelta(seconds=1))

    assert _ask(client, article, 4).status_code == 202
    assert len(queued) == 1


@pytest.mark.django_db
def test_a_particle_asks_about_its_verb(client, haiku, queued):
    article = _article()

    _ask(client, article, 5)  # "mit"
    _work(queued)
    assert _ask(client, article, 2).json()["status"] == "done"  # "teilte": same question

    assert len(haiku.requests) == 1
    assert '"teilte … mit"' in haiku.requests[0]["messages"][0]["content"]
    assert WordQuestion.objects.get().index == 2


@pytest.mark.django_db
@pytest.mark.parametrize("level, index", [("b1", 6), ("b1", 9), ("b1", 99), ("a2", 0), ("c1", 0)])
def test_only_words_of_a_summary_can_be_asked_about(client, queued, level, index):
    # 6 is ".", 9 is "373", 99 doesn't exist, A2 has no words, C1 isn't a level.
    article = _article()
    assert _ask(client, article, index, level=level).status_code == 404
    assert _poll(client, article, index, level=level).status_code == 404
    assert queued == []


@pytest.mark.django_db
def test_an_article_without_a_finished_deep_dive_cant_be_asked_about(client, queued):
    article = _article()
    Article.objects.filter(pk=article.pk).update(deep_dive_status="running")
    assert _ask(client, article, 4).status_code == 404


@pytest.mark.django_db
def test_asking_needs_a_post_with_the_csrf_token(queued):
    article = _article()
    strict = Client(enforce_csrf_checks=True)

    assert _ask(strict, article, 4).status_code == 403
    assert Client().put(f"/articles/{article.pk}/words/b1/4/ask/").status_code == 405

    page = strict.get(f"/articles/{article.pk}/").content.decode()
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
    response = strict.post(f"/articles/{article.pk}/words/b1/4/ask/", HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 202


@pytest.mark.django_db
def test_asking_is_rate_limited_per_client_ip_but_polling_isnt(client, queued, monkeypatch):
    monkeypatch.setattr(views, "ASK_WORD_RATE", "2/h")
    article = _article()

    assert [_ask(client, article, i).status_code for i in (0, 1, 3)] == [202, 202, 429]
    assert _ask(client, article, 3, ip="203.0.113.8").status_code == 202
    assert [_poll(client, article, 0).status_code for _ in range(3)] == [200, 200, 200]


@pytest.mark.django_db
def test_asking_stops_at_the_daily_cap_but_saved_answers_still_show(client, haiku, queued, settings):
    settings.DEEP_DIVE_DAILY_CAP_USD = Decimal("0.10")
    article = _article()
    _ask(client, article, 4)
    _work(queued)  # costs 0.11, over the cap

    refused = _ask(client, article, 1)
    assert refused.status_code == 503
    assert "daily limit" in refused.json()["error"]
    assert _ask(client, article, 4).json()["status"] == "done"  # saved, free
    assert queued == []


@pytest.mark.django_db
def test_the_worker_checks_the_cap_again(client, haiku, queued, settings):
    article = _article()
    _ask(client, article, 4)
    settings.DEEP_DIVE_DAILY_CAP_USD = Decimal("0")  # spent meanwhile

    _work(queued)

    assert haiku.requests == []
    assert _poll(client, article, 4).json() == {"status": "failed", "error": "Couldn't get an answer. Try again."}


@pytest.mark.django_db
def test_questions_count_toward_todays_spending():
    article = _article()
    WordQuestion.objects.create(article=article, level="b1", index=1, word="Firma", sentence="",
                                answer="a", cost_usd=Decimal("0.5"), asked_at=NOW)
    WordQuestion.objects.create(article=article, level="b1", index=4, word="Zahlen", sentence="",
                                answer="a", cost_usd=Decimal("7"), asked_at=NOW - timedelta(days=1))
    assert tasks.spent_today() == Decimal("0.5")


@pytest.mark.django_db
@pytest.mark.parametrize("respond, cost", [
    (lambda: _reply(stop_reason="max_tokens"), CALL_COST),
    (lambda: (_ for _ in ()).throw(anthropic.APIConnectionError(request=REQUEST)), Decimal(0)),
])
def test_a_failed_call_can_be_asked_again_and_its_cost_counts(client, monkeypatch, queued, respond, cost):
    fake = FakeAnthropic(respond)
    monkeypatch.setattr(word_questions, "anthropic_client", lambda **kwargs: fake)
    article = _article()

    _ask(client, article, 4)
    _work(queued)

    assert _poll(client, article, 4).json() == {"status": "failed", "error": "Couldn't get an answer. Try again."}
    question = WordQuestion.objects.get()
    assert (question.status, question.answer, question.cost_usd) == ("failed", "", cost)

    fake._respond = lambda: _reply()
    assert _ask(client, article, 4).status_code == 202
    _work(queued)
    assert _poll(client, article, 4).json() == {"status": "done", "answer": "It means: announced."}
    assert WordQuestion.objects.get().cost_usd == cost + CALL_COST


@pytest.mark.django_db
def test_without_an_api_key_the_worker_fails_the_question(client, queued, settings):
    settings.ANTHROPIC_API_KEY = ""
    article = _article()

    assert _ask(client, article, 4).status_code == 202  # the displayer doesn't need the key
    _work(queued)

    assert WordQuestion.objects.get().status == "failed"


@pytest.mark.django_db
def test_the_worker_skips_a_question_that_is_no_longer_waiting(haiku):
    question = WordQuestion.objects.create(article=_article(), level="b1", index=4, word="Zahlen", sentence="",
                                           status="done", answer="a", asked_at=NOW)
    assert word_questions.answer_question(question.pk) is False
    assert haiku.requests == []


@pytest.mark.django_db
def test_the_page_carries_saved_answers_for_the_reader(client):
    article = _article()
    WordQuestion.objects.create(article=article, level="b1", index=2, word="teilte … mit", sentence="",
                                status="done", answer="announced </script><b>", cost_usd=0, asked_at=NOW)
    body = client.get(f"/articles/{article.pk}/").content.decode()

    assert f'data-ask-url="/articles/{article.pk}/words/b1/0/ask/"' in body
    blob = re.search(r'<script id="reader-details-b1" type="application/json">(.*?)</script>', body, re.S).group(1)
    details = json.loads(blob)
    assert details["words"]["2"]["answer"] == "announced </script><b>"
    assert details["words"]["4"]["answer"] == ""
