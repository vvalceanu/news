import json
import re

import pytest

from articles import reading, words_subprocess
from articles.tests.test_words import _done_article
from dictionary.models import Entry


@pytest.fixture(scope="module")
def nlp():
    return words_subprocess.load()


def _text(paragraphs):
    return "\n\n".join("".join(t["text"] + t["ws"] for t in p) for p in paragraphs)


def test_grammar_in_words():
    assert reading.grammar("Mood=Ind|Number=Sing|Person=3|Tense=Pres|VerbForm=Fin") == "3rd person, singular, present, indicative"
    assert reading.grammar("Mood=Sub|Number=Sing|Person=3") == "3rd person, singular, subjunctive (Konjunktiv)"
    assert reading.grammar("Case=Dat|Gender=Fem|Number=Sing|PronType=Art") == "singular, feminine, dative"
    assert reading.grammar("") == ""


@pytest.mark.django_db
def test_paragraphs_give_back_the_text_split_at_blank_lines(nlp):
    text = "Die Firma teilte die Zahlen mit.  Es gibt 373 Stellen.\n\nZweiter Absatz:\n„Neu“ – mit Tab\tdazwischen."
    paragraphs = reading.build(words_subprocess.analyze(nlp, text))["paragraphs"]

    assert len(paragraphs) == 2
    assert _text(paragraphs) == "Die Firma teilte die Zahlen mit. Es gibt 373 Stellen.\n\nZweiter Absatz: „Neu“ – mit Tab dazwischen."
    clickable = {t["text"]: t["clickable"] for p in paragraphs for t in p}
    assert clickable["teilte"] and clickable["mit"] and clickable["Stellen"]
    assert not clickable["373"] and not clickable["."] and not clickable["„"]


@pytest.mark.django_db
def test_details_join_a_separable_verb_and_list_entries_once(nlp):
    Entry.objects.create(word="mitteilen", pos="verb", meanings=[{"gloss": "to inform", "tags": []}])
    Entry.objects.create(word="Zahl", pos="noun", gender="f", plural="Zahlen", meanings=[{"gloss": "number", "tags": []}])
    words = words_subprocess.analyze(nlp, "Die Firma teilte die Zahlen mit. Die Zahlen sind gut.")
    details = reading.build(words)["details"]
    by_text = {}
    for i, word in enumerate(words):
        by_text.setdefault(word["text"], i)

    teilte = details["words"][by_text["teilte"]]
    assert teilte["text"] == "teilte … mit"
    assert teilte["parts"] == [by_text["teilte"], by_text["mit"]]
    assert teilte["separable"] is True
    assert teilte["lemma"] == "mitteilen"
    assert details["words"][by_text["mit"]] == {"see": by_text["teilte"]}

    zahlen = details["words"][by_text["Zahlen"]]
    assert zahlen["pos"] == "noun"
    assert "plural" in zahlen["grammar"]
    # Both "Zahlen" point to the same entry, which is listed once.
    [entry_id] = {m["entry"] for i, w in details["words"].items() if w.get("lemma") == "Zahl" for m in w["matches"]}
    assert details["entries"][entry_id] == {
        "word": "Zahl", "article": "die", "plural": "Zahlen", "class": "noun", "swiss": False, "meanings": ["number"],
    }
    assert details["words"][by_text["Firma"]]["matches"] == []  # not in this test's dictionary
    assert by_text["."] not in details["words"]


@pytest.mark.django_db
def test_page_renders_clickable_words_and_their_details(client):
    Entry.objects.create(word="mitteilen", pos="verb", meanings=[{"gloss": "to <inform>", "tags": []}])
    article = _done_article(words_b1=[
        {"text": "<b>teilte</b>", "ws": " ", "lemma": "mitteilen", "verb": None, "known": True,
         "pos": "VERB", "tag": "VVFIN", "dep": "ROOT", "morph": "Mood=Ind|Tense=Past"},
        {"text": "mit", "ws": "", "lemma": "mitteilen", "verb": 0, "known": True,
         "pos": "ADP", "tag": "PTKVZ", "dep": "svp", "morph": ""},
        {"text": ".", "ws": "", "lemma": "", "verb": None, "known": False,
         "pos": "PUNCT", "tag": "$.", "dep": "punct", "morph": ""},
    ])
    body = client.get(f"/articles/{article.pk}/").content.decode()

    # The words are escaped and sit next to each other with only their own whitespace.
    assert ('<p><span class="w" data-i="0" aria-controls="word-panel-b1">&lt;b&gt;teilte&lt;/b&gt;</span> '
            '<span class="w" data-i="1" aria-controls="word-panel-b1">mit</span>.</p>') in body
    assert 'id="word-panel-b1"' in body and "reader.js" in body
    # The details are JSON in a script tag that can't be closed early by the data.
    blob = re.search(r'<script id="reader-details-b1" type="application/json">(.*?)</script>', body, re.S).group(1)
    assert "<" not in blob
    details = json.loads(blob)
    assert details["words"]["0"]["text"] == "<b>teilte</b> … mit"
    assert details["words"]["0"]["grammar"] == "past, indicative"
    [entry] = details["entries"].values()
    assert entry["meanings"] == ["to <inform>"]
    # A2 has no words: its German is the plain summary, still next to the English.
    assert "Die Firma sagt: Es gibt weniger Stellen." in body
    assert "The company says: there are fewer jobs." in body
