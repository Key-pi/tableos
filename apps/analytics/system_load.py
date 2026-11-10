"""The platform's load (PQ-051, owner 2026-11-11).

``take_snapshot`` — once a minute (beat ``analytics.record_system_snapshot``):
the server (``/proc`` — the whole machine, also from inside a container),
PostgreSQL, the Celery queues in Redis and the requests and bot updates of
the last whole minute (``core/load_counter.py``). Every reading is cheap and
any of them may fail: its values stay empty. Kept ``KEEP_DAYS``.

``load_report`` — what the platform's page shows: now, warnings in plain
words and charts for a day (10-minute points) or a week (hourly points).
Warnings are only on the page; messages to Telegram are the future
monitoring bot (PQ-039).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.db.models import Sum
from django.utils import timezone

from apps.analytics.models import PartnerDailyMetric, SystemSnapshot
from apps.partners.models import BotInstance, Partner
from core import load_counter
from core.i18n import _
from core.i18n import gettext_lazy as _lazy

logger = logging.getLogger(__name__)

KEEP_DAYS = 8
QUEUES = {"queue_default": "default", "queue_broadcasts": "broadcasts"}

# Warnings (owner 2026-11-11: red on the page only).
STALE_MINUTES = 3
CPU_WARN = 85.0  # % on average over the last 5 minutes
MEMORY_WARN = 90.0
DISK_WARN = 85.0
DB_CONNECTIONS_WARN = 0.8  # of max_connections
LONG_QUERY_WARN = 60.0  # seconds
QUEUE_WARN = 200  # tasks waiting: notifications are late
CAPACITY_WARN = 0.8  # of the partners the load test held

PERIODS = {
    "day": (_lazy("Сутки"), timedelta(minutes=10), 144),
    "week": (_lazy("Неделя"), timedelta(hours=1), 168),
}
WORKING_BOT_STATES = (
    BotInstance.RuntimeState.RUNNING,
    BotInstance.RuntimeState.RUNNING_SUSPENDED,
)


# --- readings ---------------------------------------------------------------


def read_host(proc_root: str = "/proc", disk_path: str = "/") -> dict:
    """Processor ticks, load, memory and disk of the machine."""

    host: dict = {"cpu_count": os.cpu_count()}
    try:
        with open(os.path.join(proc_root, "stat")) as stat:
            ticks = [int(value) for value in stat.readline().split()[1:]]
        idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)  # idle + iowait
        host["cpu_total"] = sum(ticks[:8])
        host["cpu_busy"] = host["cpu_total"] - idle
    except (OSError, ValueError, IndexError):
        pass
    try:
        memory = {}
        with open(os.path.join(proc_root, "meminfo")) as meminfo:
            for line in meminfo:
                name, _sep, rest = line.partition(":")
                memory[name] = int(rest.split()[0])
        total = memory["MemTotal"]
        host["memory_percent"] = round(100 * (total - memory["MemAvailable"]) / total, 1)
    except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError):
        pass
    try:
        with open(os.path.join(proc_root, "loadavg")) as loadavg:
            host["load_1"] = float(loadavg.read().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    try:
        disk = shutil.disk_usage(disk_path)
        host["disk_percent"] = round(100 * disk.used / disk.total, 1)
    except (OSError, ZeroDivisionError):
        pass
    return host


def read_database() -> dict:
    """Connections, size and the longest running query (PostgreSQL only)."""

    if connection.vendor != "postgresql":
        return {}
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*), "
                "coalesce(max(extract(epoch FROM now() - query_start)) "
                "FILTER (WHERE state = 'active' AND pid <> pg_backend_pid()), 0), "
                "pg_database_size(current_database()), "
                "current_setting('max_connections')::int "
                "FROM pg_stat_activity WHERE datname = current_database()"
            )
            count, longest, size, maximum = cursor.fetchone()
    except Exception:  # noqa: BLE001 — the page shows «unknown»
        logger.warning("Database load not read", exc_info=True)
        return {}
    return {
        "db_connections": count, "db_max_connections": maximum, "db_size": size,
        "longest_query_seconds": round(float(longest), 1),
    }


def read_queues(client=None) -> dict:
    """Tasks waiting in the Celery queues (lists in the broker's Redis)."""

    try:
        if client is None:
            import redis

            client = redis.Redis.from_url(
                settings.CELERY_BROKER_URL, socket_timeout=0.5, socket_connect_timeout=0.5
            )
        return {field: int(client.llen(name)) for field, name in QUEUES.items()}
    except Exception:  # noqa: BLE001
        return {}


def active_partners() -> int:
    return Partner.objects.filter(status=Partner.Status.ACTIVE).count()


# --- the snapshot -----------------------------------------------------------


def _traffic(minute: int, client) -> dict:
    counts = load_counter.read_minute(minute, client=client) if client is not None else (
        load_counter.read_minute(minute) if load_counter.enabled() else {}
    )
    result = {"http_requests": counts.get("http", 0), "bot_updates": counts.get("bot", 0)}
    for kind, field in (("http", "http_avg_ms"), ("bot", "bot_avg_ms")):
        if counts.get(kind):
            result[field] = round(counts.get(f"{kind}_ms", 0) / counts[kind], 1)
    return result


def take_snapshot(*, now=None, host=None, database=None, queues=None,
                  traffic_client=None) -> SystemSnapshot:
    """One snapshot for this minute (taken again — replaced), older than
    ``KEEP_DAYS`` removed."""

    now = now or timezone.now()
    minute = now.replace(second=0, microsecond=0)
    host = read_host() if host is None else host
    values = {
        "cpu_busy": host.get("cpu_busy"), "cpu_total": host.get("cpu_total"),
        "cpu_count": host.get("cpu_count"), "load_1": host.get("load_1"),
        "memory_percent": host.get("memory_percent"),
        "disk_percent": host.get("disk_percent"), "cpu_percent": None,
        **(read_database() if database is None else database),
        **(read_queues() if queues is None else queues),
        **_traffic(int(now.timestamp()) // 60 - 1, traffic_client),
    }
    previous = (
        SystemSnapshot.objects.filter(taken_at__lt=minute)
        .exclude(cpu_total=None).order_by("-taken_at").first()
    )
    if previous is not None and values["cpu_total"] is not None:
        ticks = values["cpu_total"] - previous.cpu_total
        if ticks > 0:
            busy = values["cpu_busy"] - previous.cpu_busy
            values["cpu_percent"] = round(min(100.0, max(0.0, 100 * busy / ticks)), 1)
    snapshot, _created = SystemSnapshot.objects.update_or_create(
        taken_at=minute, defaults=values
    )
    SystemSnapshot.objects.filter(taken_at__lt=now - timedelta(days=KEEP_DAYS)).delete()
    return snapshot


# --- the page ---------------------------------------------------------------


def _size(value: int | None) -> str:
    if value is None:
        return "—"
    if value >= 1024 ** 3:
        return _("{size} ГБ", size=f"{value / 1024 ** 3:.1f}")
    return _("{size} МБ", size=f"{value / 1024 ** 2:.0f}")


def _number(value, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        value = f"{value:.0f}" if value >= 10 else f"{value:.1f}"
    return f"{value}{suffix}"


def _warnings(latest, recent, stale, bots_failed, partners, capacity) -> list[str]:
    found = []
    if stale is not None and stale > STALE_MINUTES:
        found.append(_(
            "Фоновые задачи не работают: последний снимок {minutes} мин назад — "
            "проверьте worker и beat.", minutes=stale,
        ))
    if latest is not None:
        loads = [row.cpu_percent for row in recent if row.cpu_percent is not None]
        if loads and sum(loads) / len(loads) > CPU_WARN:
            found.append(_("Процессор загружен на {percent}% в среднем за 5 минут.",
                           percent=round(sum(loads) / len(loads))))
        if (latest.memory_percent or 0) > MEMORY_WARN:
            found.append(_("Память занята на {percent}%.",
                           percent=round(latest.memory_percent)))
        if (latest.disk_percent or 0) > DISK_WARN:
            found.append(_("Диск заполнен на {percent}% — освободите место или увеличьте диск.",
                           percent=round(latest.disk_percent)))
        if latest.db_max_connections and (
            latest.db_connections or 0
        ) >= DB_CONNECTIONS_WARN * latest.db_max_connections:
            found.append(_("Заняты {used} из {limit} соединений с базой.",
                           used=latest.db_connections, limit=latest.db_max_connections))
        if (latest.longest_query_seconds or 0) > LONG_QUERY_WARN:
            found.append(_("Самый долгий запрос к базе идёт уже {seconds} с.",
                           seconds=round(latest.longest_query_seconds)))
        if (latest.queue_default or 0) > QUEUE_WARN:
            found.append(_("В очереди задач {count} — уведомления запаздывают.",
                           count=latest.queue_default))
    if bots_failed:
        found.append(_("Не работают боты: {count} — причина в разделе «Боты».",
                       count=bots_failed))
    if capacity and partners >= CAPACITY_WARN * capacity:
        found.append(_(
            "Работающих партнёров {partners} из {capacity}, которые сервер держал на "
            "нагрузочном тесте, — пора готовить сервер мощнее.",
            partners=partners, capacity=capacity,
        ))
    return found


def _bucket_start(moment: datetime, size: timedelta) -> datetime:
    seconds = int(size.total_seconds())
    return datetime.fromtimestamp(
        int(moment.timestamp()) // seconds * seconds, tz=moment.tzinfo
    )


def _average(values) -> float | None:
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 1) if values else None


def _weighted(rows, count_field: str, avg_field: str) -> float | None:
    pairs = [(getattr(row, count_field), getattr(row, avg_field)) for row in rows
             if getattr(row, avg_field) is not None and getattr(row, count_field)]
    total = sum(count for count, _avg in pairs)
    return round(sum(count * avg for count, avg in pairs) / total, 1) if total else None


def _maximum(values) -> int | None:
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _charts(now: datetime, period: str) -> tuple[list[str], list[dict]]:
    _title, size, points = PERIODS[period]
    first = _bucket_start(now, size) - size * (points - 1)
    buckets: list[list[SystemSnapshot]] = [[] for _index in range(points)]
    for row in SystemSnapshot.objects.filter(taken_at__gte=first).only(
        "taken_at", "cpu_percent", "memory_percent", "http_requests", "http_avg_ms",
        "bot_updates", "bot_avg_ms", "queue_default", "queue_broadcasts",
    ):
        index = int((row.taken_at - first) / size)
        if 0 <= index < points:
            buckets[index].append(row)
    label_format = "%H:%M" if period == "day" else "%d.%m %H:00"
    labels = [
        timezone.localtime(first + size * index).strftime(label_format)
        for index in range(points)
    ]

    def per_minute(rows, field):
        return round(sum(getattr(row, field) for row in rows) / len(rows), 1) if rows else None

    def chart(title, series, suffix=""):
        # Unfold's chart: the y axis with values shows when a dataset asks.
        data = {"labels": labels, "datasets": [
            {"label": str(label), "data": values, "borderColor": color,
             "backgroundColor": color, "pointRadius": 0, "spanGaps": False,
             "displayYAxis": True, "suffixYAxis": suffix, "maxTicksXLimit": 12}
            for label, values, color in series
        ]}
        return {
            "title": title,
            "legend": [(str(label), color) for label, _values, color in series],
            "data": json.dumps(data, cls=DjangoJSONEncoder),
        }

    primary, second = "var(--color-primary-600)", "#f59e0b"
    return labels, [
        chart(_("Процессор и память, %"), [
            (_("Процессор"), [_average(r.cpu_percent for r in rows) for rows in buckets], primary),
            (_("Память"), [_average(r.memory_percent for r in rows) for rows in buckets], second),
        ], "%"),
        chart(_("Нагрузка в минуту"), [
            (_("Запросы сайта и Mini App"),
             [per_minute(rows, "http_requests") for rows in buckets], primary),
            (_("Обновления ботов"), [per_minute(rows, "bot_updates") for rows in buckets], second),
        ]),
        chart(_("Время ответа, мс"), [
            (_("Сайт и Mini App"),
             [_weighted(rows, "http_requests", "http_avg_ms") for rows in buckets], primary),
            (_("Боты"), [_weighted(rows, "bot_updates", "bot_avg_ms") for rows in buckets],
             second),
        ], _("мс")),
        chart(_("Очереди фоновых задач"), [
            (_("Уведомления и задачи"),
             [_maximum(r.queue_default for r in rows) for rows in buckets], primary),
            (_("Рассылки"), [_maximum(r.queue_broadcasts for r in rows) for rows in buckets],
             second),
        ]),
    ]


def load_report(*, now=None, period: str = "day") -> dict:
    now = now or timezone.now()
    period = period if period in PERIODS else "day"
    recent = list(SystemSnapshot.objects.order_by("-taken_at")[:5])
    latest = recent[0] if recent else None
    stale = int((now - latest.taken_at).total_seconds() // 60) if latest else None
    bots = BotInstance.objects.filter(is_active=True)
    bots_running = bots.filter(runtime_state__in=WORKING_BOT_STATES).count()
    bots_failed = bots.exclude(
        runtime_state__in=(*WORKING_BOT_STATES, BotInstance.RuntimeState.UNKNOWN)
    ).count()
    partners = active_partners()
    capacity = getattr(settings, "PLATFORM_CAPACITY_PARTNERS", 0)
    orders_today = PartnerDailyMetric.objects.filter(
        bucket_date=timezone.localdate(now)
    ).aggregate(total=Sum("orders_count"))["total"] or 0
    labels, charts = _charts(now, period)
    tiles = []
    if latest is not None:
        tiles = [
            (_("Процессор"), _number(latest.cpu_percent, "%"),
             _("ядер: {count}", count=latest.cpu_count or "—")),
            (_("Память"), _number(latest.memory_percent, "%"), ""),
            (_("Диск"), _number(latest.disk_percent, "%"), ""),
            (_("Соединения с базой"),
             f"{_number(latest.db_connections)} / {_number(latest.db_max_connections)}",
             _("база: {size}", size=_size(latest.db_size))),
            (_("Запросы в минуту"), _number(latest.http_requests),
             _("ответ: {ms} мс", ms=_number(latest.http_avg_ms))),
            (_("Обновления ботов в минуту"), _number(latest.bot_updates),
             _("ответ: {ms} мс", ms=_number(latest.bot_avg_ms))),
            (_("Очередь задач"), _number(latest.queue_default),
             _("рассылки: {count}", count=_number(latest.queue_broadcasts))),
        ]
    tiles += [
        (_("Боты"), _("{running} работают", running=bots_running),
         _("с ошибкой: {count}", count=bots_failed)),
        (_("Работающих партнёров"), str(partners),
         _("сервер держал {capacity}", capacity=capacity)),
        (_("Заказов сегодня"), str(orders_today), _("по всем партнёрам")),
    ]
    return {
        "latest": latest,
        "stale_minutes": stale,
        "warnings": _warnings(latest, recent, stale, bots_failed, partners, capacity),
        "tiles": tiles,
        "labels": labels,
        "charts": charts,
        "period": period,
        "periods": [(key, title) for key, (title, _size, _points) in PERIODS.items()],
    }
