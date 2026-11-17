"""Word analysis of German text: spaCy for tokens and grammar, simplemma for lemmas.

Runs as its own short-lived process (see `articles/words.py`), so the ~400 MB that spaCy
and simplemma's dictionary take is given back when it exits. It never imports Django and
gets no env vars: it can't see secrets or the database.

stdin:  {"<key>": "<German text>", ...}            (JSON)
stdout: {"<key>": [<word>, ...], ...}              (JSON, ASCII-escaped)

A word is one spaCy token. Joining every `text` + `ws` gives back the input exactly, so the
page can render from the words alone. Fields:
  text, ws   the token and the whitespace after it
  lemma      dictionary form; "" for punctuation, numbers and whitespace. A separable verb
             gets its particle glued on ("teilte ... mit" -> "mitteilen"), and so does the particle
  verb       for a separable particle, the index of its verb; otherwise null
  known      whether simplemma's dictionary has the lemma (False: compound, rare word or name)
  pos, tag, dep   spaCy's part of speech, STTS tag and dependency label, for debugging
  morph      spaCy's morphology as a Universal Dependencies FEATS string, e.g. "Mood=Sub|Tense=Pres"
"""

import json
import sys

import spacy
from simplemma import is_known, lemmatize

MODEL = "de_core_news_sm"
LANG = "de"


def load():
    # NER only finds names, which the part of speech covers well enough; excluding it saves time.
    return spacy.load(MODEL, exclude=["ner"])


def is_particle(token) -> bool:
    return token.dep_ == "svp"


def _simplemma(surface: str) -> str:
    # Summaries are Swiss (ss, never ß). simplemma knows most Swiss spellings; for one it
    # doesn't, try the ß spelling ("Fussball" -> "Fußball"). Only then, because "Masse",
    # "Busse" and "dass" are words of their own.
    if "ss" in surface and not is_known(surface, LANG):
        eszett = surface.replace("ss", "ß")
        if is_known(eszett, LANG):
            return lemmatize(eszett, LANG)
    return lemmatize(surface, LANG)


def _base_lemma(token, has_particle: bool) -> str:
    if not any(c.isalpha() for c in token.text):
        return ""  # punctuation, numbers, whitespace
    # A token with a separable particle is a verb, even when mistagged as a noun ("Mach die Tür zu").
    if token.pos_ == "PROPN" and not has_particle:
        return token.text
    # Nouns keep their capital; everything else is looked up lowercased (sentence starts).
    surface = token.text if token.pos_ == "NOUN" and not has_particle else token.lower_
    return _simplemma(surface)


def analyze(nlp, text: str) -> list[dict]:
    doc = nlp(text)
    particles = {}
    for token in doc:
        if is_particle(token):
            particles.setdefault(token.head.i, []).append(token)

    lemmas = []
    for token in doc:
        lemma = _base_lemma(token, token.i in particles)
        if token.i in particles:
            prefix = "".join(p.lower_ for p in particles[token.i])
            lemma = prefix + lemma.lower()
            if not is_known(lemma, LANG):
                # simplemma has no context and sometimes picks the wrong verb ("fällt" -> "fällen").
                alternative = prefix + token.lemma_.lower()
                if is_known(alternative, LANG):
                    lemma = alternative
        lemmas.append(lemma)

    words = []
    for token in doc:
        verb = token.head.i if is_particle(token) else None
        lemma = lemmas[verb] if verb is not None else lemmas[token.i]
        words.append(
            {
                "text": token.text,
                "ws": token.whitespace_,
                "lemma": lemma,
                "verb": verb,
                "known": bool(lemma) and is_known(lemma, LANG),
                "pos": token.pos_,
                "tag": token.tag_,
                "dep": token.dep_,
                "morph": str(token.morph),
            }
        )
    return words


def main():
    texts = json.load(sys.stdin.buffer)
    if not isinstance(texts, dict) or not all(isinstance(t, str) for t in texts.values()):
        sys.exit("expected a JSON object of strings on stdin")
    nlp = load()
    # ASCII-escaped, so the parent can read it whatever this process's locale is.
    json.dump({key: analyze(nlp, text) for key, text in texts.items()}, sys.stdout)


if __name__ == "__main__":
    main()
