from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django_q.models import Schedule

from articles import tasks
from articles.models import Article
from articles.sources import SourceUnavailable
from articles.tests.fakes import NOW, FakeMiniflux
from articles.tests.fakes import entry as _entry


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    monkeypatch.setattr(tasks.timezone, "now", lambda: NOW)


def _hours_ago(hours):
    return NOW - timedelta(hours=hours)


def _stored_ids():
    return sorted(Article.objects.values_list("miniflux_id", flat=True))


@pytest.mark.django_db
def test_sweep_copies_entry_fields():
    assert tasks.sweep(FakeMiniflux([_entry(2)])) == 1

    article = Article.objects.get(miniflux_id=2)
    assert article.title == "Titel 2"
    assert article.url == "https://example.com/2"
    assert article.feed_title == "Stadtnachrichten"
    assert article.published_at == NOW - timedelta(hours=1)
    assert article.content == "<p>Text 2</p>"


@pytest.mark.django_db
def test_sweep_adds_at_most_the_cap_taking_the_oldest_first(monkeypatch):
    monkeypatch.setattr(tasks, "MAX_NEW_PER_SWEEP", 2)
    # Ids deliberately out of date order: the oldest entries are 3 and 1.
    source = FakeMiniflux(
        [_entry(1, published_at=_hours_ago(5)), _entry(2, published_at=_hours_ago(1)), _entry(3, published_at=_hours_ago(9))]
    )

    assert tasks.sweep(source) == 2

    assert _stored_ids() == [1, 3]


@pytest.mark.django_db
def test_next_sweep_continues_with_the_next_oldest(monkeypatch):
    monkeypatch.setattr(tasks, "MAX_NEW_PER_SWEEP", 2)
    source = FakeMiniflux([_entry(i, published_at=_hours_ago(10 - i)) for i in range(1, 6)])

    tasks.sweep(source)
    tasks.sweep(source)

    assert _stored_ids() == [1, 2, 3, 4]


@pytest.mark.django_db
def test_sweep_skips_entries_older_than_max_age():
    old = NOW - tasks.MAX_AGE - timedelta(minutes=1)
    source = FakeMiniflux([_entry(1, published_at=old), _entry(2)])

    tasks.sweep(source)

    assert _stored_ids() == [2]
    assert source.calls[0][0] == NOW - tasks.MAX_AGE


@pytest.mark.django_db
def test_sweep_picks_up_an_entry_that_appeared_late_with_a_lower_id():
    tasks.sweep(FakeMiniflux([_entry(2)]))

    assert tasks.sweep(FakeMiniflux([_entry(1), _entry(2)])) == 1
    assert _stored_ids() == [1, 2]


@pytest.mark.django_db
def test_sweep_pages_past_entries_already_stored(monkeypatch):
    monkeypatch.setattr(tasks, "PAGE_SIZE", 2)
    monkeypatch.setattr(tasks, "MAX_NEW_PER_SWEEP", 2)
    source = FakeMiniflux([_entry(i, published_at=_hours_ago(10 - i)) for i in range(1, 6)])
    tasks.sweep(source)
    source.calls.clear()

    tasks.sweep(source)

    assert _stored_ids() == [1, 2, 3, 4]
    assert [offset for _, _, offset in source.calls] == [0, 2]


@pytest.mark.django_db
def test_sweep_stops_paging_after_max_pages(monkeypatch):
    monkeypatch.setattr(tasks, "PAGE_SIZE", 1)
    monkeypatch.setattr(tasks, "MAX_PAGES", 2)
    source = FakeMiniflux([_entry(i, published_at=_hours_ago(10 - i)) for i in range(1, 6)])

    assert tasks.sweep(source) == 2
    assert len(source.calls) == 2


@pytest.mark.django_db
def test_sweep_does_not_add_the_same_entry_twice_when_pages_overlap(monkeypatch):
    """Offset paging can return an entry twice if Miniflux gains entries between requests."""
    monkeypatch.setattr(tasks, "PAGE_SIZE", 2)

    class ShiftingSource(FakeMiniflux):
        def entries_published_after(self, published_after, limit, offset=0):
            return super().entries_published_after(published_after, limit, max(offset - 1, 0))

    source = ShiftingSource([_entry(i, published_at=_hours_ago(10 - i)) for i in range(1, 4)])

    assert tasks.sweep(source) == 3
    assert _stored_ids() == [1, 2, 3]


@pytest.mark.django_db
def test_pending_lists_every_entry_not_yet_stored_oldest_first():
    tasks.sweep(FakeMiniflux([_entry(2, published_at=_hours_ago(5))]))
    source = FakeMiniflux(
        [_entry(1, published_at=_hours_ago(1)), _entry(2, published_at=_hours_ago(5)), _entry(3, published_at=_hours_ago(9))]
    )

    assert [e.id for e in tasks.pending(source, limit=100)] == [3, 1]


@pytest.mark.django_db
def test_sweep_is_scheduled():
    schedule = Schedule.objects.get(name=tasks.SWEEP_SCHEDULE_NAME)

    assert schedule.func == "articles.tasks.run_sweep"
    assert schedule.schedule_type == Schedule.MINUTES
    assert schedule.repeats == -1


@pytest.mark.django_db
def test_sweep_command_runs_a_sweep(monkeypatch):
    monkeypatch.setattr(tasks, "get_source", lambda: FakeMiniflux([_entry(1)]))
    out = StringIO()

    call_command("sweep", stdout=out)

    assert Article.objects.count() == 1
    assert "1" in out.getvalue()


@pytest.mark.django_db
def test_sweep_command_reports_unavailable_source_in_one_line(monkeypatch):
    class DownSource:
        def entries_published_after(self, *args, **kwargs):
            raise SourceUnavailable("ConnectTimeout")

    monkeypatch.setattr(tasks, "get_source", lambda: DownSource())

    with pytest.raises(CommandError, match="Miniflux unavailable: ConnectTimeout"):
        call_command("sweep")
