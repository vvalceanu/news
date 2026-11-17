from django.db import models


class Article(models.Model):
    """Our own copy of a news entry. Everything after the sweep reads this table, never Miniflux."""

    miniflux_id = models.BigIntegerField(unique=True)
    title = models.TextField()
    url = models.URLField(max_length=2000, blank=True)
    feed_title = models.CharField(max_length=500, blank=True)
    published_at = models.DateTimeField(db_index=True)
    content = models.TextField(blank=True)
    # English title from Haiku; empty until translated. A title gets one attempt: a failure is
    # logged and left alone (set attempts back to 0 in the admin to try again).
    title_en = models.TextField(blank=True, default="")
    title_en_attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    # Deep dive: German summaries at B1 and A2 (one Sonnet call each), each with an English
    # translation for reference. An article goes through it at most once: a failed call leaves
    # that level's fields empty and the article is still DONE (`manage.py deep_dive --force` redoes it).
    class DeepDive(models.TextChoices):
        NONE = "", "Not requested"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"

    deep_dive_status = models.CharField(max_length=10, choices=DeepDive.choices, default=DeepDive.NONE, blank=True)
    deep_dive_requested_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # What the deep dive's Claude calls cost, from the API's token counts. The daily cap sums this.
    deep_dive_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    summary_b1 = models.TextField(blank=True, default="")
    summary_b1_en = models.TextField(blank=True, default="")
    summary_a2 = models.TextField(blank=True, default="")
    summary_a2_en = models.TextField(blank=True, default="")
    # Each German summary as a list of words (spaCy tokens with lemmas, see
    # articles/words_subprocess.py). Empty until analyzed, or if the analysis failed.
    words_b1 = models.JSONField(default=list, blank=True)
    words_a2 = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self):
        return self.title


class WordQuestion(models.Model):
    """Haiku's answer to "what does this word mean in this sentence?", asked from the reader.

    One row per word of a summary (the browser sends only the word's position; the word and its
    sentence come from our stored words). Everyone who opens the word later sees the saved
    answer. A failed call leaves `answer` empty, so it can be asked again; its cost still counts.
    """

    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="word_questions")
    level = models.CharField(max_length=2)  # "a2" or "b1"
    index = models.PositiveIntegerField()  # the word's position in words_a2 / words_b1
    word = models.TextField()  # as asked, e.g. "teilte … mit"
    sentence = models.TextField()
    answer = models.TextField(blank=True, default="")
    # All calls for this word, failed ones included. Counts against the daily cap on the day of
    # the latest call (`asked_at`).
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    asked_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["article", "level", "index"], name="one_question_per_word"),
        ]

    def __str__(self):
        return f"{self.word} ({self.article_id} {self.level})"
