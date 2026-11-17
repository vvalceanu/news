"""Reading English Wiktionary's German entries, as extracted by wiktextract (kaikki.org).

The data is JSON Lines, one object per (word, word class, origin), described at
https://kaikki.org/dictionary/rawdata.html. Swiss German isn't a language of its own there:
Swiss words are German entries with a `Switzerland` tag, and the Swiss ss spellings are German
entries that point to the ß spelling.
"""

import gzip
import json
import os
import urllib.request
from collections.abc import Iterator
from pathlib import Path

from .models import Entry, Redirect

# All languages, about 3 GB gzipped. kaikki.org also has a German-only file, but it's deprecated
# (wiktextract issue #1178), so we filter this one ourselves.
RAW_DUMP_URL = "https://kaikki.org/dictionary/raw-wiktextract-data.jsonl.gz"
LANG_CODE = "de"

GENDERS = {"masculine": "m", "feminine": "f", "neuter": "n"}
SWISS = {"Switzerland", "Liechtenstein"}
MAX_WORD = Entry._meta.get_field("word").max_length
MAX_POS = Entry._meta.get_field("pos").max_length
MAX_PLURAL = Entry._meta.get_field("plural").max_length


def fetch_german(url: str, out: Path) -> tuple[int, int]:
    """Stream the dump at `url` and write its German entries to `out` (gzipped JSON Lines).

    Never holds more than one line in memory. Writes to a temporary file first, so a failed
    download leaves an existing `out` alone. Returns (lines read, German entries kept).
    """
    tmp = out.with_name(out.name + ".part")
    read = kept = 0
    request = urllib.request.Request(url, headers={"User-Agent": "news-dictionary-import"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, gzip.GzipFile(fileobj=response) as lines, \
                gzip.open(tmp, "wb") as dest:
            for line in lines:
                read += 1
                # Most lines aren't German; skip them without parsing. Non-German entries can
                # still mention "de" (translations), so the parsed check below decides.
                if b'"lang_code": "de"' not in line and b'"lang_code":"de"' not in line:
                    continue
                if json.loads(line).get("lang_code") == LANG_CODE:
                    dest.write(line)
                    kept += 1
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return read, kept


def read_entries(path: Path) -> Iterator[dict]:
    """The German entries of a file written by `fetch_german` (or kaikki.org's German file)."""
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            entry = json.loads(line)
            if entry.get("lang_code") == LANG_CODE:
                yield entry


def _gloss(sense: dict) -> str:
    # `glosses` lists the parent meaning first and the specific sub-meaning last.
    glosses = sense.get("glosses") or []
    return glosses[-1].strip() if glosses else ""


def _is_redirect(sense: dict) -> bool:
    return bool(sense.get("form_of") or sense.get("alt_of") or {"form-of", "alt-of"} & set(sense.get("tags", ())))


def rows(entry: dict) -> tuple[Entry | None, list[Redirect]]:
    """The dictionary rows for one Wiktionary entry (not saved).

    The Entry holds the senses with a real meaning; it's None if there are none. Each sense that
    only points elsewhere ("plural of Stelle") becomes a Redirect, one per target and kind.
    """
    word, pos = entry.get("word", ""), entry.get("pos", "")
    if not word or len(word) > MAX_WORD or not pos or len(pos) > MAX_POS:
        return None, []
    senses = entry.get("senses", [])

    redirects: dict[tuple[str, str], list[str]] = {}
    for sense in filter(_is_redirect, senses):
        if sense.get("form_of") or "form-of" in sense.get("tags", ()):
            kind, targets = Redirect.Kind.FORM, sense.get("form_of") or []
        else:
            kind, targets = Redirect.Kind.ALT, sense.get("alt_of") or []
        target = (targets[0].get("word") or "").strip() if targets else ""
        if target and len(target) <= MAX_WORD:
            redirects.setdefault((target, kind), []).append(_gloss(sense))
    redirect_rows = [
        Redirect(word=word, pos=pos, target=target, kind=kind, note="; ".join(dict.fromkeys(filter(None, notes))))
        for (target, kind), notes in redirects.items()
    ]

    real = [s for s in senses if not _is_redirect(s) and _gloss(s) and "no-gloss" not in s.get("tags", ())]
    if not real:
        return None, redirect_rows
    tags = {tag for sense in real for tag in sense.get("tags", ())}
    gender = plural = ""
    if pos == "noun":
        gender = "".join(sorted({GENDERS[tag] for tag in tags if tag in GENDERS}))
        # The headword's own forms have no `source`; the declension table's do.
        forms = [f["form"] for f in entry.get("forms", []) if f.get("tags") == ["plural"] and "source" not in f]
        plural = ", ".join(dict.fromkeys(forms))[:MAX_PLURAL]
    meanings = [
        {"gloss": _gloss(s), "tags": [tag for tag in s.get("tags", ()) if tag not in GENDERS]} for s in real
    ]
    return (
        Entry(word=word, pos=pos, gender=gender, plural=plural, meanings=meanings, swiss=bool(tags & SWISS)),
        redirect_rows,
    )
