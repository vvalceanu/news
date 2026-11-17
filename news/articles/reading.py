"""The reader: a summary rendered from its words, each clickable, with the details per word.

The page gets the paragraphs (for the template) and, once, a JSON blob with each word's
details, which `static/articles/reader.js` shows when a word is clicked. Dictionary entries
are listed once in the blob and referenced by id, since `der` or `und` come up many times.
"""

from dictionary import lookup
from dictionary.models import Entry

PARTS_OF_SPEECH = {
    "NOUN": "noun",
    "PROPN": "name",
    "VERB": "verb",
    "AUX": "auxiliary verb",
    "ADJ": "adjective",
    "ADV": "adverb",
    "ADP": "preposition",
    "DET": "article or determiner",
    "PRON": "pronoun",
    "CCONJ": "conjunction",
    "SCONJ": "conjunction",
    "PART": "particle",
    "INTJ": "interjection",
}
WORD_CLASSES = {
    "adj": "adjective",
    "adv": "adverb",
    "prep": "preposition",
    "postp": "postposition",
    "pron": "pronoun",
    "det": "determiner",
    "conj": "conjunction",
    "name": "name",
    "intj": "interjection",
    "num": "number",
    "prep_phrase": "prepositional phrase",
}
ARTICLES = {"m": "der", "f": "die", "n": "das"}

# spaCy's morphology (Universal Dependencies FEATS), in the order it's shown.
# Features not listed here (PronType, Definite, VerbForm=Fin, Degree=Pos, …) aren't shown.
FEATURES = [
    ("Person", {"1": "1st person", "2": "2nd person", "3": "3rd person"}),
    ("Number", {"Sing": "singular", "Plur": "plural"}),
    ("Gender", {"Masc": "masculine", "Fem": "feminine", "Neut": "neuter"}),
    ("Case", {"Nom": "nominative", "Acc": "accusative", "Dat": "dative", "Gen": "genitive"}),
    ("Tense", {"Pres": "present", "Past": "past"}),
    ("Mood", {"Ind": "indicative", "Sub": "subjunctive (Konjunktiv)", "Imp": "imperative"}),
    ("VerbForm", {"Inf": "infinitive", "Part": "participle"}),
    ("Degree", {"Cmp": "comparative", "Sup": "superlative"}),
    ("Reflex", {"Yes": "reflexive"}),
]


def grammar(morph: str) -> str:
    """'Mood=Ind|Number=Sing|Person=3|Tense=Pres|VerbForm=Fin' → '3rd person, singular, present, indicative'."""
    feats = dict(part.split("=", 1) for part in morph.split("|") if "=" in part)
    shown = [names[feats[key]] for key, names in FEATURES if feats.get(key) in names]
    return ", ".join(shown)


def _clickable(word: dict) -> bool:
    return bool(word.get("lemma"))


def _paragraphs(words: list[dict]) -> list[list[dict]]:
    """The words grouped into paragraphs, split at blank lines. Whitespace inside a paragraph
    becomes a single space, which is all HTML would show anyway."""
    paragraphs, current, gap = [], [], ""
    for i, word in enumerate(words):
        if not word["text"].strip():  # spaCy keeps runs of whitespace as tokens of their own
            gap += word["text"] + word["ws"]
            continue
        if current:
            if gap.count("\n") >= 2:
                paragraphs.append(current)
                current = []
            else:
                current[-1]["ws"] = " " if gap else ""
        current.append({"i": i, "text": word["text"], "ws": "", "clickable": _clickable(word)})
        gap = word["ws"]
    if current:
        paragraphs.append(current)
    return paragraphs


def _entry(entry: Entry) -> dict:
    article = "/".join(ARTICLES[g] for g in entry.gender if g in ARTICLES)
    return {
        "word": entry.word,
        "article": article,
        "plural": entry.plural,
        "class": WORD_CLASSES.get(entry.pos, entry.pos.replace("_", " ")),
        "swiss": entry.swiss,
        "meanings": [m["gloss"] for m in entry.meanings],
    }


def build(words: list[dict], answers: dict[int, str] | None = None) -> dict:
    """{"paragraphs": [[{"i", "text", "ws", "clickable"}]], "rows": lookup rows (debug table),
    "details": {"words": {i: …}, "entries": {id: …}}} for one summary. `answers` are the saved
    "Ask AI" answers by word position, shown without asking again."""
    answers = answers or {}
    rows = lookup.annotate(words)
    particles: dict[int, list[int]] = {}
    for i, word in enumerate(words):
        if word.get("verb") is not None:
            particles.setdefault(word["verb"], []).append(i)

    entries: dict[int, dict] = {}
    details: dict[int, dict] = {}
    for row in rows:
        i, word = row["i"], row["word"]
        if not _clickable(word):
            continue
        if row["see"] is not None:  # a separable particle shows its verb
            details[i] = {"see": row["see"]}
            continue
        parts = [i, *particles.get(i, [])]
        matches = []
        for match in row["matches"] or []:
            for entry in match["entries"]:
                entries.setdefault(entry.pk, _entry(entry))
                matches.append({"entry": entry.pk, "via": match["via"], "fits": match["fits"]})
        details[i] = {
            "text": " … ".join(words[p]["text"] for p in parts),
            "parts": parts,
            "lemma": word["lemma"],
            "pos": PARTS_OF_SPEECH.get(word.get("pos"), ""),
            "separable": len(parts) > 1,
            "grammar": grammar(word.get("morph", "")),
            "matches": matches,
            "answer": answers.get(i, ""),
        }
    return {
        "paragraphs": _paragraphs(words),
        "rows": rows,
        "details": {"words": details, "entries": entries},
    }
