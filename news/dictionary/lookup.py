"""Matching a summary's words (see articles/words_subprocess.py) to dictionary entries.

Looked up when a page renders, so a newer dictionary import shows up without reanalyzing
articles. A summary costs three queries, whatever its length.

For each word, the dictionary words tried are:
1. its lemma;
2. the ß word for a Swiss ss spelling, of the word as written, of its lemma, or of the word an
   inflected form points to (`Strassen` → `Strasse` → `Straße`). This is *added* to the lemma,
   not used instead: `Masse` is both a word (mass) and the Swiss spelling of `Maße`;
3. if nothing of the right word class matched yet: other variants and contractions (`im` → `in`;
   `am` is also a particle, so it gets both);
4. only if nothing matched at all, and not for names: the word an inflected form points to (for
   forms simplemma didn't reduce), then the lemma with its first letter's case flipped (a noun
   mistagged as something else, or the reverse). For names these give nonsense (`Gallen` →
   "plural of Galle", bile).

Each match keeps the entries whose word class fits spaCy's part of speech, or all of the
word's entries if none fits.
"""

from collections import defaultdict

from .models import Entry, Redirect

# Wiktionary's word classes that fit each of spaCy's (Universal Dependencies) parts of speech.
WORD_CLASSES = {
    "NOUN": {"noun"},
    "PROPN": {"name", "noun"},
    "VERB": {"verb"},
    "AUX": {"verb"},
    "ADJ": {"adj"},
    "ADV": {"adv", "adj"},  # German adjectives double as adverbs
    "ADP": {"prep", "postp", "circumpos"},
    "DET": {"article", "det", "pron"},
    "PRON": {"pron", "det"},
    "CCONJ": {"conj"},
    "SCONJ": {"conj"},
    "PART": {"particle", "adv"},
    "INTJ": {"intj"},
}
# Punctuation, numbers, symbols and spaces aren't looked up.
SKIPPED = {"PUNCT", "NUM", "SYM", "SPACE", "X"}


def _looked_up(word: dict) -> bool:
    return bool(word.get("lemma")) and word.get("pos") not in SKIPPED and word.get("verb") is None


def _is_swiss(redirect: Redirect) -> bool:
    return redirect.kind == Redirect.Kind.ALT and "Switzerland" in redirect.note


def _flip_case(text: str) -> str:
    return text[:1].lower() + text[1:] if text[:1].isupper() else text[:1].upper() + text[1:]


def annotate(words: list[dict]) -> list[dict]:
    """One row per word: {"i", "word", "see", "matches"}.

    `see` is the verb's index for a separable particle (its match is on the verb's row).
    `matches` is None for words that aren't looked up (punctuation, numbers, particles),
    otherwise a list, empty if the dictionary has nothing, of
    {"word": dictionary word, "via": how it was reached ("" for the lemma), "entries": [Entry], "fits": bool}.
    """
    looked_up = [w for w in words if _looked_up(w)]
    sources = {s for w in looked_up for s in (w["text"], w["lemma"])}
    redirects = defaultdict(list)
    for redirect in Redirect.objects.filter(word__in=sources):
        redirects[redirect.word].append(redirect)
    # Second hop: Swiss spellings of the words that inflected forms point to.
    form_targets = {r.target for rs in redirects.values() for r in rs if r.kind == Redirect.Kind.FORM}
    for redirect in Redirect.objects.filter(word__in=form_targets - sources):
        redirects[redirect.word].append(redirect)

    candidates = sources | {_flip_case(w["lemma"]) for w in looked_up}
    candidates |= {r.target for rs in redirects.values() for r in rs}
    entries = defaultdict(list)
    for entry in Entry.objects.filter(word__in=candidates):
        entries[entry.word].append(entry)

    rows = []
    for i, word in enumerate(words):
        row = {"i": i, "word": word, "see": word.get("verb"), "matches": None}
        if _looked_up(word):
            row["matches"] = _matches(word, entries, redirects)
        rows.append(row)
    return rows


def _matches(word: dict, entries: dict, redirects: dict) -> list[dict]:
    lemma, text = word["lemma"], word["text"]
    fitting_classes = WORD_CLASSES.get(word.get("pos"), set())
    matches: list[dict] = []

    def add(dictionary_word: str, via: str) -> None:
        if any(m["word"] == dictionary_word for m in matches) or not entries.get(dictionary_word):
            return
        found = entries[dictionary_word]
        fitting = [e for e in found if e.pos in fitting_classes]
        matches.append({"word": dictionary_word, "via": via, "entries": fitting or found, "fits": bool(fitting)})

    def form_targets(source: str) -> list[str]:
        return [r.target for r in redirects.get(source, ()) if r.kind == Redirect.Kind.FORM]

    add(lemma, "")
    for source in dict.fromkeys([text, lemma, *form_targets(text)]):
        for redirect in redirects.get(source, ()):
            if _is_swiss(redirect):
                add(redirect.target, f"Swiss spelling of {redirect.target}")
    if not any(m["fits"] for m in matches):
        for source in dict.fromkeys([lemma, text]):
            for redirect in redirects.get(source, ()):
                if redirect.kind == Redirect.Kind.ALT:
                    add(redirect.target, redirect.note or f"variant of {redirect.target}")
    if matches or word.get("pos") == "PROPN":
        return matches
    for redirect in redirects.get(text, ()):
        if redirect.kind == Redirect.Kind.FORM:
            add(redirect.target, redirect.note or f"form of {redirect.target}")
    if not matches:
        add(_flip_case(lemma), "other case")
    return matches
