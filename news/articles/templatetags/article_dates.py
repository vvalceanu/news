from datetime import datetime, timedelta

from django import template
from django.utils import timezone

register = template.Library()

_WEEKDAYS = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."]
# Duden-style abbreviations; short months are written out.
_MONTHS = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."]


def relative_day(value: datetime | None, now: datetime | None = None) -> str:
    """'Heute, 13:45', 'Gestern, 07:20', otherwise 'Sa., 31. Okt., 13:45' (plus the year if it
    isn't this year), all in the site's TIME_ZONE."""
    if value is None:
        return ""
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    local = timezone.localtime(value)
    today = timezone.localtime(now).date()
    clock = f"{local:%H:%M}"
    day = local.date()
    if day == today:
        return f"Heute, {clock}"
    if day == today - timedelta(days=1):
        return f"Gestern, {clock}"
    label = f"{_WEEKDAYS[day.weekday()]}, {day.day}. {_MONTHS[day.month - 1]}"
    if day.year != today.year:
        label += f" {day.year}"
    return f"{label}, {clock}"


@register.filter(name="relative_day")
def relative_day_filter(value):
    return relative_day(value)
