"""Venue opening hours: is the venue open at a moment, and how to describe it.

Intervals live in ``OpeningHours`` in the venue's local time
(``Partner.timezone``). An interval whose ``closes_at`` is at or before
``opens_at`` runs past midnight into the next day. A venue without intervals
is always open.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from apps.partners.models import OpeningHours, Partner

_SHORT_DAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _intervals(partner: Partner) -> list[OpeningHours]:
    return list(OpeningHours.objects.filter(partner=partner).order_by("weekday", "opens_at"))


def _covers(interval: OpeningHours, *, weekday: int, moment: time) -> bool:
    overnight = interval.closes_at <= interval.opens_at
    if interval.weekday == weekday:
        if overnight:
            return moment >= interval.opens_at
        return interval.opens_at <= moment < interval.closes_at
    # The tail of yesterday's overnight interval.
    if overnight and interval.weekday == (weekday - 1) % 7:
        return moment < interval.closes_at
    return False


def is_open_at(partner: Partner, moment: datetime, *, intervals=None) -> bool:
    intervals = _intervals(partner) if intervals is None else intervals
    if not intervals:
        return True
    local = moment.astimezone(ZoneInfo(partner.timezone))
    weekday = local.weekday()
    local_time = local.time().replace(tzinfo=None)
    return any(_covers(interval, weekday=weekday, moment=local_time) for interval in intervals)


def has_opening_hours(partner: Partner) -> bool:
    return OpeningHours.objects.filter(partner=partner).exists()


def describe_opening_hours(partner: Partner) -> str:
    """Human text such as ``Пн 10:00–22:00, Пт 18:00–03:00``; empty if none."""

    intervals = _intervals(partner)
    if not intervals:
        return ""
    by_day: dict[int, list[str]] = {}
    for interval in intervals:
        by_day.setdefault(interval.weekday, []).append(
            f"{interval.opens_at:%H:%M}–{interval.closes_at:%H:%M}"
        )
    parts = [f"{_SHORT_DAYS[day]} {', '.join(spans)}" for day, spans in sorted(by_day.items())]
    return "; ".join(parts)


def local_time_label(partner: Partner, moment: datetime) -> str:
    return moment.astimezone(ZoneInfo(partner.timezone)).strftime("%H:%M")


def minutes_from(moment: datetime, minutes: int) -> datetime:
    return moment + timedelta(minutes=minutes)
