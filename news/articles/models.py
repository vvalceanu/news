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

    class Meta:
        ordering = ["-published_at"]

    def __str__(self):
        return self.title
