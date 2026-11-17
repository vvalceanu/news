import pytest

from articles.tests.test_words import _done_article
from dictionary import lookup
from dictionary.models import Entry, Redirect


def _word(text, lemma, pos, verb=None):
    return {"text": text, "ws": " ", "lemma": lemma, "verb": verb, "known": True, "pos": pos,
            "tag": "", "dep": "", "morph": ""}


def _entry(word, pos, *glosses, **fields):
    return Entry.objects.create(word=word, pos=pos, meanings=[{"gloss": g, "tags": []} for g in glosses], **fields)


def _matched(row):
    return [(m["word"], [e.pos for e in m["entries"]], m["fits"], m["via"]) for m in row["matches"]]


@pytest.fixture
def dictionary(db):
    _entry("streichen", "verb", "to stroke", "to cancel")
    _entry("Stelle", "noun", "place", "job", gender="f", plural="Stellen")
    _entry("stellen", "verb", "to put")
    _entry("Strass", "noun", "rhinestone")
    _entry("Straße", "noun", "street")
    _entry("in", "prep", "in")
    _entry("an", "prep", "at")
    _entry("am", "particle", "precedes the superlative")
    _entry("Masse", "noun", "mass")
    _entry("Maße", "noun", "measurements")
    _entry("Zürich", "name", "Zurich")
    _entry("Galle", "noun", "bile")
    _entry("Personalfirma", "noun", "staffing agency")
    _entry("Beispiel", "noun", "example")
    Redirect.objects.create(word="Strasse", pos="noun", target="Straße", kind="alt",
                            note="Switzerland and Liechtenstein standard spelling of Straße")
    Redirect.objects.create(word="Strassen", pos="noun", target="Strasse", kind="form", note="plural of Strasse")
    Redirect.objects.create(word="Masse", pos="noun", target="Maße", kind="alt",
                            note="Switzerland and Liechtenstein standard spelling of Maße")
    Redirect.objects.create(word="im", pos="contraction", target="in", kind="alt", note="contraction of in + dem")
    Redirect.objects.create(word="am", pos="contraction", target="an", kind="alt", note="contraction of an + dem")
    Redirect.objects.create(word="Personalfirmen", pos="noun", target="Personalfirma", kind="form",
                            note="plural of Personalfirma")
    Redirect.objects.create(word="Gallen", pos="noun", target="Galle", kind="form", note="plural of Galle")


def test_a_word_matches_its_lemma_and_only_entries_of_its_word_class(dictionary):
    [row] = lookup.annotate([_word("Stellen", "Stelle", "NOUN")])
    assert _matched(row) == [("Stelle", ["noun"], True, "")]
    assert [m["gloss"] for m in row["matches"][0]["entries"][0].meanings] == ["place", "job"]


def test_a_word_of_another_class_still_shows_its_entries(dictionary):
    [row] = lookup.annotate([_word("streichen", "streichen", "NOUN")])
    assert _matched(row) == [("streichen", ["verb"], False, "")]


def test_a_swiss_spelling_adds_the_eszett_word(dictionary):
    # simplemma reads "Strasse" as the plural of Strass; the Swiss spelling adds Straße.
    [row] = lookup.annotate([_word("Strasse", "Strass", "NOUN")])
    assert _matched(row) == [
        ("Strass", ["noun"], True, ""),
        ("Straße", ["noun"], True, "Swiss spelling of Straße"),
    ]
    # A real word that is also a Swiss spelling keeps its own meaning first.
    [row] = lookup.annotate([_word("Masse", "Masse", "NOUN")])
    assert [m["word"] for m in row["matches"]] == ["Masse", "Maße"]


def test_a_swiss_spelling_is_found_through_an_inflected_form(dictionary):
    [row] = lookup.annotate([_word("Strassen", "Strassen", "NOUN")])
    assert _matched(row) == [("Straße", ["noun"], True, "Swiss spelling of Straße")]


def test_contractions_go_to_the_preposition(dictionary):
    [im] = lookup.annotate([_word("im", "im", "ADP")])
    assert _matched(im) == [("in", ["prep"], True, "contraction of in + dem")]
    # "am" is also a particle, which doesn't fit ADP, so the contraction is added.
    [am] = lookup.annotate([_word("am", "am", "ADP")])
    assert _matched(am) == [("am", ["particle"], False, ""), ("an", ["prep"], True, "contraction of an + dem")]


def test_an_unreduced_inflected_form_falls_back_to_its_lemma(dictionary):
    [row] = lookup.annotate([_word("Personalfirmen", "Personalfirmen", "NOUN")])
    assert _matched(row) == [("Personalfirma", ["noun"], True, "plural of Personalfirma")]


def test_the_other_case_is_tried_last(dictionary):
    [row] = lookup.annotate([_word("beispiel", "beispiel", "ADV")])
    assert _matched(row) == [("Beispiel", ["noun"], False, "other case")]


def test_names_match_only_themselves(dictionary):
    zurich, gallen = lookup.annotate([_word("Zürich", "Zürich", "PROPN"), _word("Gallen", "Gallen", "PROPN")])
    assert _matched(zurich) == [("Zürich", ["name"], True, "")]
    assert gallen["matches"] == []  # not "plural of Galle"


def test_punctuation_numbers_and_particles_are_not_looked_up(dictionary):
    rows = lookup.annotate([
        _word("streicht", "streichen", "VERB"),
        _word("373", "", "NUM"),
        _word(".", "", "PUNCT"),
        _word("durch", "durchstreichen", "ADP", verb=0),
        _word("Quatschwort", "Quatschwort", "NOUN"),
    ])
    assert [(r["i"], r["see"], r["matches"] is None) for r in rows] == [
        (0, None, False), (1, None, True), (2, None, True), (3, 0, True), (4, None, False),
    ]
    assert rows[4]["matches"] == []


def test_a_summary_costs_three_queries(dictionary, django_assert_num_queries):
    words = [_word("Strassen", "Strassen", "NOUN"), _word("im", "im", "ADP"), _word("Stellen", "Stelle", "NOUN")]
    with django_assert_num_queries(3):
        lookup.annotate(words)


def test_the_debug_table_shows_the_dictionary(dictionary, client):
    article = _done_article(words_b1=[
        _word("Stellen", "Stelle", "NOUN"),
        _word("Quatschwort", "Quatschwort", "NOUN"),
        _word(".", "", "PUNCT"),
    ])
    body = client.get(f"/articles/{article.pk}/").content.decode()

    assert "noun f, pl. <span lang=\"de\">Stellen</span>" in body
    assert "<li>job</li>" in body
    assert "not in the dictionary" in body
