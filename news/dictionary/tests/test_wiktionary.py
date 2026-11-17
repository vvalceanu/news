import gzip
import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from dictionary import wiktionary
from dictionary.models import Entry, Redirect

# Trimmed real entries from the kaikki.org dump.
STREICHEN = {
    "word": "streichen", "pos": "verb", "lang_code": "de",
    "senses": [
        {"glosses": ["to stroke"], "tags": ["class-1", "strong", "transitive"]},
        {"glosses": ["to cancel; to discard; to delete; to strike out; to cross out"]},
    ],
}
STELLE = {
    "word": "Stelle", "pos": "noun", "lang_code": "de",
    "forms": [
        {"form": "Stelle", "tags": ["genitive"]},
        {"form": "Stellen", "tags": ["plural"]},
        {"form": "de-ndecl", "source": "declension", "tags": ["inflection-template"]},
        {"form": "Stellen", "source": "declension", "tags": ["plural"]},
    ],
    "senses": [
        {"glosses": ["location, spot"], "tags": ["feminine"]},
        {"glosses": ["location, spot", "spot (on skin)"], "tags": ["feminine"]},
    ],
}
VELO = {
    "word": "Velo", "pos": "noun", "lang_code": "de",
    "forms": [{"form": "Velos", "tags": ["plural"]}],
    "senses": [{"glosses": ["bicycle"], "tags": ["Southern-Germany", "Switzerland", "neuter", "strong"]}],
}
STRASSE_ALT = {
    "word": "Strasse", "pos": "noun", "lang_code": "de",
    "senses": [{
        "glosses": ["Switzerland and Liechtenstein standard spelling of Straße"],
        "tags": ["Liechtenstein", "Switzerland", "alt-of", "feminine", "standard"],
        "alt_of": [{"word": "Straße"}],
    }],
}
STELLEN_FORMS = {
    "word": "Stellen", "pos": "noun", "lang_code": "de",
    "senses": [
        {"glosses": ["genitive singular of Stelle"], "tags": ["form-of"], "form_of": [{"word": "Stelle"}]},
        {"glosses": ["plural of Stelle"], "tags": ["form-of", "plural"], "form_of": [{"word": "Stelle"}]},
    ],
}
NO_GLOSS = {"word": "Leer", "pos": "noun", "lang_code": "de", "senses": [{"tags": ["no-gloss"]}]}
# An English entry whose translations mention German: it must not count as German.
ENGLISH = {
    "word": "bicycle", "pos": "noun", "lang_code": "en",
    "senses": [{"glosses": ["a vehicle"]}],
    "translations": [{"lang_code": "de", "word": "Velo"}],
}
GERMAN = [STREICHEN, STELLE, VELO, STRASSE_ALT, STELLEN_FORMS, NO_GLOSS]


def _write(path, entries):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def test_fetch_keeps_only_german_entries(tmp_path):
    dump = _write(tmp_path / "dump.jsonl.gz", [ENGLISH, *GERMAN, {"word": "vélo", "lang_code": "fr", "pos": "noun"}])
    out = tmp_path / "de.jsonl.gz"

    read, kept = wiktionary.fetch_german(dump.as_uri(), out)

    assert (read, kept) == (8, len(GERMAN))
    assert [e["word"] for e in wiktionary.read_entries(out)] == [e["word"] for e in GERMAN]
    assert not (tmp_path / "de.jsonl.gz.part").exists()


def test_a_failed_fetch_keeps_the_old_file(tmp_path):
    out = _write(tmp_path / "de.jsonl.gz", [VELO])
    broken = tmp_path / "broken.gz"
    broken.write_bytes(b"not gzip")

    with pytest.raises(OSError):
        wiktionary.fetch_german(broken.as_uri(), out)

    assert [e["word"] for e in wiktionary.read_entries(out)] == ["Velo"]
    assert not (tmp_path / "de.jsonl.gz.part").exists()


def test_rows_for_a_verb_keep_the_meanings_in_order():
    entry, redirects = wiktionary.rows(STREICHEN)
    assert (entry.word, entry.pos, entry.gender, entry.plural, entry.swiss) == ("streichen", "verb", "", "", False)
    assert [m["gloss"] for m in entry.meanings] == ["to stroke", "to cancel; to discard; to delete; to strike out; to cross out"]
    assert entry.meanings[0]["tags"] == ["class-1", "strong", "transitive"]
    assert redirects == []


def test_rows_for_a_noun_have_gender_plural_and_the_specific_sub_meaning():
    entry, _ = wiktionary.rows(STELLE)
    assert (entry.gender, entry.plural) == ("f", "Stellen")  # the declension table's plural isn't repeated
    assert [m["gloss"] for m in entry.meanings] == ["location, spot", "spot (on skin)"]
    assert entry.meanings[0]["tags"] == []  # gender is its own field


def test_rows_mark_swiss_words():
    entry, _ = wiktionary.rows(VELO)
    assert (entry.gender, entry.plural, entry.swiss) == ("n", "Velos", True)


def test_a_swiss_spelling_becomes_a_redirect_to_the_eszett_word():
    entry, redirects = wiktionary.rows(STRASSE_ALT)
    assert entry is None
    [redirect] = redirects
    assert (redirect.word, redirect.target, redirect.kind) == ("Strasse", "Straße", "alt")
    assert redirect.note == "Switzerland and Liechtenstein standard spelling of Straße"


def test_inflected_forms_become_one_redirect_per_target():
    entry, [redirect] = wiktionary.rows(STELLEN_FORMS)
    assert entry is None
    assert (redirect.target, redirect.kind) == ("Stelle", "form")
    assert redirect.note == "genitive singular of Stelle; plural of Stelle"


def test_entries_without_meanings_or_too_long_are_skipped():
    assert wiktionary.rows(NO_GLOSS) == (None, [])
    assert wiktionary.rows({**VELO, "word": "x" * 101}) == (None, [])


@pytest.mark.django_db
def test_import_replaces_the_dictionary(tmp_path):
    Entry.objects.create(word="alt", pos="noun")
    path = _write(tmp_path / "de.jsonl.gz", GERMAN)
    out = StringIO()

    call_command("import_wiktionary", path, stdout=out)

    assert sorted(Entry.objects.values_list("word", flat=True)) == ["Stelle", "Velo", "streichen"]
    assert sorted(Redirect.objects.values_list("word", "target")) == [("Stellen", "Stelle"), ("Strasse", "Straße")]
    assert "Imported 3 entries and 2 redirects." in out.getvalue()


@pytest.mark.django_db
def test_import_of_a_file_without_german_entries_keeps_the_old_dictionary(tmp_path):
    Entry.objects.create(word="Velo", pos="noun")
    path = _write(tmp_path / "en.jsonl.gz", [ENGLISH])

    with pytest.raises(CommandError, match="unchanged"):
        call_command("import_wiktionary", path, stdout=StringIO())
    with pytest.raises(CommandError, match="No file"):
        call_command("import_wiktionary", tmp_path / "missing.gz", stdout=StringIO())

    assert list(Entry.objects.values_list("word", flat=True)) == ["Velo"]


@pytest.mark.django_db
def test_pages_credit_wiktionary(client):
    body = client.get("/").content.decode()
    assert "English Wiktionary" in body
    assert "https://creativecommons.org/licenses/by-sa/4.0/" in body
