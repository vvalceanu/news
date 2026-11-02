from datetime import datetime, timedelta, timezone

from articles.sources import Article as SourceArticle

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def entry(id, published_at=NOW - timedelta(hours=1), **overrides):
    fields = dict(
        id=id,
        title=f"Titel {id}",
        url=f"https://example.com/{id}",
        feed_title="Stadtnachrichten",
        published_at=published_at,
        content=f"<p>Text {id}</p>",
    )
    fields.update(overrides)
    return SourceArticle(**fields)


class FakeMiniflux:
    """In-memory stand-in that filters like the Miniflux API does."""

    def __init__(self, entries=(), error=None):
        self.entries = sorted(entries, key=lambda e: e.id)
        self.error = error
        self.calls = []

    def entries_published_after(self, published_after, limit, offset=0):
        self.calls.append((published_after, limit, offset))
        if self.error:
            raise self.error
        matching = sorted(
            (e for e in self.entries if e.published_at > published_after), key=lambda e: (e.published_at, e.id)
        )
        return matching[offset : offset + limit]
