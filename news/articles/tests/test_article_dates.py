from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from articles.templatetags.article_dates import relative_day

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 3, 18, 0, tzinfo=BERLIN)


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        (datetime(2026, 11, 3, 13, 45, tzinfo=BERLIN), "Heute, 13:45"),
        (datetime(2026, 11, 3, 0, 0, tzinfo=BERLIN), "Heute, 00:00"),
        (datetime(2026, 11, 2, 7, 20, tzinfo=BERLIN), "Gestern, 07:20"),
        (datetime(2026, 11, 2, 23, 59, tzinfo=BERLIN), "Gestern, 23:59"),
        (datetime(2026, 11, 1, 9, 5, tzinfo=BERLIN), "So., 1. Nov., 09:05"),
        (datetime(2026, 3, 15, 12, 0, tzinfo=BERLIN), "So., 15. März, 12:00"),
        (datetime(2025, 12, 31, 22, 0, tzinfo=BERLIN), "Mi., 31. Dez. 2025, 22:00"),
    ],
)
def test_relative_day(published, expected):
    assert relative_day(published, now=NOW) == expected


def test_relative_day_uses_site_time_zone_not_utc():
    # 23:30 UTC on 2 Nov is 00:30 on 3 Nov in Berlin.
    assert relative_day(datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc), now=NOW) == "Heute, 00:30"


def test_relative_day_treats_naive_datetimes_as_site_time():
    assert relative_day(datetime(2026, 11, 3, 13, 45), now=NOW) == "Heute, 13:45"


def test_relative_day_handles_missing_value():
    assert relative_day(None, now=NOW) == ""
