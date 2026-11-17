"""German→English dictionary from English Wiktionary (CC BY-SA 4.0, via kaikki.org).

Filled by `manage.py import_wiktionary`, which replaces both tables. Nothing else writes them.
"""

from django.db import models


class Entry(models.Model):
    """One Wiktionary entry for a German word: a word class and its meanings.

    A word can have several entries: one per word class, and one per origin within a class
    (`Bank` the bench and `Bank` the bank). Rows keep Wiktionary's order, main meaning first.
    """

    word = models.CharField(max_length=100)  # as Wiktionary writes it: nouns capitalized, ß not ss
    pos = models.CharField(max_length=20)  # Wiktionary's word class: noun, verb, adj, adv, prep, name, …
    gender = models.CharField(max_length=3, blank=True)  # nouns: "m", "f", "n", or several ("mn")
    plural = models.CharField(max_length=200, blank=True)  # nouns: plural forms, comma-separated
    # [{"gloss": "to paint", "tags": ["transitive"]}, …] in Wiktionary's order.
    meanings = models.JSONField(default=list)
    swiss = models.BooleanField(default=False)  # some meaning is tagged Switzerland or Liechtenstein

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["word"])]
        verbose_name_plural = "entries"

    def __str__(self):
        return f"{self.word} ({self.pos})"


class Redirect(models.Model):
    """A German word whose Wiktionary entry only points to another word.

    FORM: an inflected form (`Stellen` → `Stelle`). ALT: a spelling variant or contraction,
    including the Swiss ss spellings (`Strasse` → `Straße`) and `im` → `in`. The same word can
    have both kinds (`Strasse` is also the plural of `Strass`).
    """

    class Kind(models.TextChoices):
        FORM = "form", "Inflected form"
        ALT = "alt", "Variant"

    word = models.CharField(max_length=100)
    pos = models.CharField(max_length=20)
    target = models.CharField(max_length=100)
    kind = models.CharField(max_length=4, choices=Kind.choices)
    # Wiktionary's wording, e.g. "Switzerland and Liechtenstein standard spelling of Straße".
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["word"])]

    def __str__(self):
        return f"{self.word} → {self.target}"
