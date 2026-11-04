"""Venue opening hours: is the venue open at a moment, and how to describe it.

Intervals live in ``OpeningHours`` in the venue's local time
(``Partner.timezone``). An interval whose ``closes_at`` is at or before
``opens_at`` runs past midnight into the next day. A venue without intervals
is always open.

Hours per module (``OpeningHours.scope``): pickup and delivery may have their
own intervals; a module without its own uses the common ones (empty scope).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from apps.partners.models import OpeningHours, Partner
from core.i18n import N_, _

_SHORT_DAYS = (N_("Пн"), N_("Вт"), N_("Ср"), N_("Чт"), N_("Пт"), N_("Сб"), N_("Вс"))


def intervals_for(partner: Partner, scope: str = "") -> list[OpeningHours]:
    rows = OpeningHours.objects.filter(partner=partner).order_by("weekday", "opens_at")
    if scope:
        own = list(rows.filter(scope=scope))
        if own:
            return own
    return list(rows.filter(scope=""))


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


def is_open_at(partner: Partner, moment: datetime, *, intervals=None, scope: str = "") -> bool:
    intervals = intervals_for(partner, scope) if intervals is None else intervals
    if not intervals:
        return True
    local = moment.astimezone(ZoneInfo(partner.timezone))
    weekday = local.weekday()
    local_time = local.time().replace(tzinfo=None)
    return any(_covers(interval, weekday=weekday, moment=local_time) for interval in intervals)


def has_opening_hours(partner: Partner, scope: str = "") -> bool:
    return bool(intervals_for(partner, scope))


def describe_opening_hours(partner: Partner, scope: str = "") -> str:
    """Human text such as ``Пн 10:00–22:00, Пт 18:00–03:00``; empty if none."""

    intervals = intervals_for(partner, scope)
    if not intervals:
        return ""
    by_day: dict[int, list[str]] = {}
    for interval in intervals:
        by_day.setdefault(interval.weekday, []).append(
            f"{interval.opens_at:%H:%M}–{interval.closes_at:%H:%M}"
        )
    days = sorted(by_day)
    if len(days) == 7 and len({tuple(spans) for spans in by_day.values()}) == 1:
        return _("ежедневно {hours}", hours=", ".join(by_day[0]))
    # Neighbouring days with the same hours read as one range: «Пн–Пт 10:00–22:00».
    groups: list[list[int]] = []
    for day in days:
        if groups and groups[-1][-1] == day - 1 and by_day[groups[-1][-1]] == by_day[day]:
            groups[-1].append(day)
        else:
            groups.append([day])
    parts = []
    for group in groups:
        label = _(_SHORT_DAYS[group[0]])
        if len(group) > 1:
            label = f"{label}–{_(_SHORT_DAYS[group[-1]])}"
        parts.append(f"{label} {', '.join(by_day[group[0]])}")
    return "; ".join(parts)


def local_time_label(partner: Partner, moment: datetime) -> str:
    return moment.astimezone(ZoneInfo(partner.timezone)).strftime("%H:%M")


def minutes_from(moment: datetime, minutes: int) -> datetime:
    return moment + timedelta(minutes=minutes)
