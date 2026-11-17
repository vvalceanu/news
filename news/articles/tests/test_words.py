import json
import sys
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from articles import tasks, words, words_subprocess
from articles.models import Article
from articles.tests.fakes import NOW


@pytest.fixture(scope="module")
def nlp():
    return words_subprocess.load()


def _by_text(analyzed):
    return {w["text"]: w for w in analyzed}


# The analysis itself, with the real spaCy model and simplemma


def test_words_give_back_the_text_exactly(nlp):
    text = "Das ist  gut.\n\nZweiter „Absatz“ – mit\tTab."
    assert "".join(w["text"] + w["ws"] for w in words_subprocess.analyze(nlp, text)) == text


def test_a_separable_verb_gets_its_particle_and_the_particle_points_to_it(nlp):
    analyzed = words_subprocess.analyze(nlp, "Das Unternehmen teilte am Montag die Zahlen mit.")
    teilte = next(i for i, w in enumerate(analyzed) if w["text"] == "teilte")
    mit = _by_text(analyzed)["mit"]

    assert analyzed[teilte]["lemma"] == "mitteilen"
    assert analyzed[teilte]["verb"] is None
    assert (mit["lemma"], mit["verb"]) == ("mitteilen", teilte)
    assert mit["known"] is True


def test_a_preposition_is_not_glued_to_the_verb(nlp):
    analyzed = _by_text(words_subprocess.analyze(nlp, "Er steht auf dem Tisch."))
    assert analyzed["steht"]["lemma"] == "stehen"
    assert analyzed["auf"]["verb"] is None


def test_lemmas_nouns_names_and_punctuation(nlp):
    analyzed = _by_text(words_subprocess.analyze(nlp, "Die Kuriere in Zürich streiken."))
    assert analyzed["Kuriere"]["lemma"] == "Kurier"
    assert analyzed["Die"]["lemma"] == "der"
    assert analyzed["Zürich"]["lemma"] == "Zürich"
    assert analyzed["."]["lemma"] == ""
    assert analyzed["."]["known"] is False


def test_swiss_spelling_falls_back_to_eszett_only_for_unknown_words(nlp):
    analyzed = _by_text(words_subprocess.analyze(nlp, "Der Fussball und die Masse."))
    assert analyzed["Fussball"]["lemma"] == "Fußball"
    assert analyzed["Masse"]["lemma"] == "Masse"  # not "Maß"


def test_morphology_is_the_feats_string(nlp):
    sei = _by_text(words_subprocess.analyze(nlp, "Das Modell sei nicht konkurrenzfähig."))["sei"]
    assert "Mood=Sub" in sei["morph"].split("|")
    assert sei["lemma"] == "sein"


# The subprocess


def test_analyze_runs_the_script_in_a_subprocess():
    analyzed = words.analyze({"a2": "Er steht auf.", "b1": ""})
    assert [w["lemma"] for w in analyzed["a2"]] == ["er", "aufstehen", "aufstehen", ""]
    assert analyzed["b1"] == []


def test_the_subprocess_gets_no_env_vars(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("not started")

    monkeypatch.setattr(words.subprocess, "run", run)
    with pytest.raises(words.AnalysisFailed, match="could not start"):
        words.analyze({"a2": "x"})

    args, kwargs = calls[0]
    assert args == [sys.executable, "-I", str(words.SCRIPT)]
    assert kwargs["env"] == {}
    assert kwargs["timeout"] == words.TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "script, error",
    [
        ("import sys; sys.exit('broken model')", "exit code 1: broken model"),
        ("print('not json')", "not JSON"),
        ("print('{\"other\": []}')", "doesn't match"),
        ("import time; time.sleep(5)", "timed out"),
    ],
)
def test_a_failing_subprocess_raises_analysis_failed(tmp_path, monkeypatch, script, error):
    fake = tmp_path / "fake.py"
    fake.write_text(script)
    monkeypatch.setattr(words, "SCRIPT", fake)
    monkeypatch.setattr(words, "TIMEOUT_SECONDS", 1)

    with pytest.raises(words.AnalysisFailed, match=error):
        words.analyze({"a2": "x"})


def test_the_script_rejects_input_that_is_not_texts(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", type("In", (), {"buffer": StringIO(json.dumps(["x"]))})())
    with pytest.raises(SystemExit):
        words_subprocess.main()


# Command and page


def _done_article(**fields):
    return Article.objects.create(
        miniflux_id=1,
        title="Titel",
        published_at=NOW - timedelta(hours=1),
        deep_dive_status="done",
        summary_b1="Die Firma teilte die Zahlen mit.",
        summary_b1_en="The company announced the figures.",
        summary_a2="Die Firma sagt: Es gibt weniger Stellen.",
        summary_a2_en="The company says: there are fewer jobs.",
        **fields,
    )


@pytest.mark.django_db
def test_command_analyzes_an_existing_article():
    article = _done_article(content="<h2>Stellenabbau</h2><p>Der Konzern <b>baut</b> 373 Stellen ab.</p>")
    out = StringIO()

    call_command("analyze_words", article.pk, stdout=out)

    article.refresh_from_db()
    assert "mitteilen" in [w["lemma"] for w in article.words_b1]
    assert "Stelle" in [w["lemma"] for w in article.words_a2]
    assert "".join(w["text"] + w["ws"] for w in article.words_original) == "Stellenabbau\n\nDer Konzern baut 373 Stellen ab."
    assert "abbauen" in [w["lemma"] for w in article.words_original]
    assert "separable verbs: mitteilen" in out.getvalue()
    assert "ORIGINAL: 6 words" in out.getvalue()  # "373" has no lemma


@pytest.mark.django_db
def test_command_fails_for_an_article_without_summaries():
    article = _done_article()
    Article.objects.filter(pk=article.pk).update(summary_b1="", summary_a2="")

    with pytest.raises(CommandError, match="Not analyzed"):
        call_command("analyze_words", article.pk, stdout=StringIO())
    with pytest.raises(CommandError, match="No article"):
        call_command("analyze_words", 999, stdout=StringIO())


@pytest.mark.django_db
def test_page_shows_the_words_debug_table_escaped(client):
    article = _done_article(
        words_b1=[
            {"text": "<b>teilte</b>", "ws": " ", "lemma": "mitteilen", "verb": None, "known": True,
             "pos": "VERB", "tag": "VVFIN", "dep": "ROOT", "morph": "Mood=Ind"},
            {"text": "mit", "ws": "", "lemma": "mitteilen", "verb": 0, "known": True,
             "pos": "ADP", "tag": "PTKVZ", "dep": "svp", "morph": ""},
            {"text": ".", "ws": "", "lemma": "", "verb": None, "known": False,
             "pos": "PUNCT", "tag": "$.", "dep": "punct", "morph": ""},
        ]
    )
    body = client.get(f"/articles/{article.pk}/").content.decode()

    assert body.count('class="words-debug"') == 2  # both show the summary as written
    assert body.count("<table>") == 1  # A2 has no words yet: no table
    assert "&lt;b&gt;teilte&lt;/b&gt;" in body
    assert "<td>→ 0</td>" in body
    assert "<td>Mood=Ind</td>" in body
    assert "<td>$.</td>" in body  # punctuation has no lemma but is listed
    assert body.count('class="unknown"') == 0  # and isn't marked as missing from the dictionary


@pytest.mark.django_db
def test_analyze_words_task_overwrites_with_fresh_words(monkeypatch):
    article = _done_article(words_b1=[{"text": "alt"}])
    monkeypatch.setattr(tasks.words, "analyze", lambda texts: {"b1": [{"text": "neu"}], "a2": []})

    assert tasks.analyze_words(article.pk) is True

    article.refresh_from_db()
    assert article.words_b1 == [{"text": "neu"}]


def _word(text, ws=" ", lemma=None, tag=""):
    return {"text": text, "ws": ws, "lemma": text.lower() if lemma is None else lemma, "verb": None,
            "known": True, "pos": "", "tag": tag, "dep": "", "morph": ""}


@pytest.mark.django_db
def test_page_shows_the_original_as_a_reader_once_analyzed(client):
    article = _done_article(
        content="<p>Die <b>Brücke</b> ist offen.</p>",
        words_original=[_word("Die"), _word("Brücke"), _word("ist"), _word("offen", ws=""), _word(".", ws="", lemma="", tag="$.")],
    )
    body = client.get(f"/articles/{article.pk}/").content.decode()

    assert "<h2>Original</h2>" in body
    assert 'data-ask-url="/articles/%d/words/original/0/ask/"' % article.pk in body
    assert '<span class="w" data-i="1" aria-controls="word-panel-original">Brücke</span>' in body
    assert 'id="reader-details-original"' in body
    assert "Click a word to see what it means." in body  # no English for the original
    assert body.count('class="words-debug"') == 3
    assert 'class="raw"' not in body  # the reader replaces the plain rendering


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["", "done"])
def test_page_shows_the_original_rendered_until_it_is_analyzed(client, status):
    article = _done_article(content="<p>Die <b>Brücke</b> ist offen.</p>")
    Article.objects.filter(pk=article.pk).update(deep_dive_status=status)

    body = client.get(f"/articles/{article.pk}/").content.decode()

    assert '<details class="raw">' in body
    assert "<p>Die <strong>Brücke</strong> ist offen.</p>" in body
    assert "<h2>Original</h2>" not in body
