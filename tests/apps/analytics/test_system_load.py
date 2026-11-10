"""The platform's load page (PQ-051, owner 2026-11-11).

A snapshot a minute (beat ``analytics.record_system_snapshot``): the server's
processor, memory and disk, PostgreSQL, the task queues and the requests and
bot updates of the last minute; kept 8 days. The page — for the platform
only — shows now, warnings in red and charts for a day and a week."""

import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.analytics import system_load
from apps.analytics.models import SystemSnapshot
from apps.partners.models import BotInstance
from apps.users.constants import AdminAccessPreset
from apps.users.models import User
from tests.apps.users.test_admin_navigation import admin_user
from tests.core.test_load_counter import FakeRedis
from tests.webapp.test_guest_api import make_venue

HOST = {"cpu_busy": 1000, "cpu_total": 4000, "load_1": 0.5, "cpu_count": 2,
        "memory_percent": 40.0, "disk_percent": 30.0}


def snapshot(minutes_ago, now, **values):
    return SystemSnapshot.objects.create(
        taken_at=(now - timedelta(minutes=minutes_ago)).replace(second=0, microsecond=0),
        **{"cpu_percent": 10.0, "memory_percent": 40.0, "disk_percent": 30.0, **values},
    )


class SnapshotTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(second=20, microsecond=0)

    def take(self, host=HOST, redis=None, **extra):
        return system_load.take_snapshot(
            now=self.now, host=host, database=extra.get("database", {}),
            queues=extra.get("queues", {}), traffic_client=redis,
        )

    def test_the_processor_load_from_the_previous_snapshot(self):
        snapshot(1, self.now, cpu_busy=500, cpu_total=2000)
        row = self.take()
        # (1000 - 500) busy of (4000 - 2000) ticks.
        self.assertEqual(row.cpu_percent, 25.0)
        self.assertEqual((row.memory_percent, row.disk_percent, row.load_1),
                         (40.0, 30.0, 0.5))

    def test_requests_and_updates_of_the_last_whole_minute(self):
        redis = FakeRedis()
        minute = int(self.now.timestamp()) // 60
        redis.hashes[f"botera:load:{minute - 1}"] = {
            "http": 30, "http_ms": 1500, "bot": 120, "bot_ms": 2400,
        }
        redis.hashes[f"botera:load:{minute}"] = {"http": 999}
        row = self.take(redis=redis, queues={"queue_default": 4, "queue_broadcasts": 0})
        self.assertEqual((row.http_requests, row.http_avg_ms), (30, 50.0))
        self.assertEqual((row.bot_updates, row.bot_avg_ms), (120, 20.0))
        self.assertEqual(row.queue_default, 4)

    def test_one_snapshot_a_minute_and_eight_days_kept(self):
        old = snapshot(8 * 24 * 60 + 5, self.now)
        self.take()
        self.take(host={**HOST, "memory_percent": 55.0})
        self.assertEqual(SystemSnapshot.objects.filter(
            taken_at=self.now.replace(second=0)).count(), 1)
        self.assertEqual(SystemSnapshot.objects.get(
            taken_at=self.now.replace(second=0)).memory_percent, 55.0)
        self.assertFalse(SystemSnapshot.objects.filter(id=old.id).exists())

    def test_a_reading_that_fails_leaves_its_values_empty(self):
        row = self.take(host={})
        self.assertIsNone(row.cpu_percent)
        self.assertIsNone(row.memory_percent)

    def test_the_beat_task_takes_a_snapshot(self):
        from apps.analytics.tasks import record_system_snapshot_task

        self.assertIn(
            "analytics.record_system_snapshot",
            [item["task"] for item in settings.CELERY_BEAT_SCHEDULE.values()],
        )
        with mock.patch.object(system_load, "read_queues", return_value={}):
            record_system_snapshot_task.apply()
        self.assertEqual(SystemSnapshot.objects.count(), 1)


class HostReadingTests(TestCase):
    def test_from_proc_files(self):
        with tempfile.TemporaryDirectory() as proc:
            Path(proc, "stat").write_text("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3 4\n")
            Path(proc, "meminfo").write_text(
                "MemTotal:  8000000 kB\nMemFree: 1000 kB\nMemAvailable:  2000000 kB\n"
            )
            Path(proc, "loadavg").write_text("1.25 0.80 0.50 1/200 300\n")
            host = system_load.read_host(proc_root=proc, disk_path=proc)
        # idle 800 + iowait 50 of 1000 ticks.
        self.assertEqual((host["cpu_busy"], host["cpu_total"]), (150, 1000))
        self.assertEqual(host["memory_percent"], 75.0)
        self.assertEqual(host["load_1"], 1.25)
        self.assertIsNotNone(host["disk_percent"])

    def test_without_proc_nothing_breaks(self):
        host = system_load.read_host(proc_root="/no/such/dir", disk_path="/no/such/dir")
        self.assertIsNone(host.get("memory_percent"))
        self.assertIsNone(host.get("cpu_total"))

    def test_queues_without_redis_are_unknown(self):
        broken = mock.Mock()
        broken.llen.side_effect = ConnectionError("down")
        self.assertEqual(system_load.read_queues(client=broken), {})

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL only")
    def test_postgresql_connections_and_size(self):
        database = system_load.read_database()
        self.assertGreaterEqual(database["db_connections"], 1)
        self.assertGreater(database["db_max_connections"], database["db_connections"])
        self.assertGreater(database["db_size"], 0)
        self.assertGreaterEqual(database["longest_query_seconds"], 0)


class ReportTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_healthy_has_no_warnings(self):
        snapshot(0, self.now, queue_default=0, db_connections=10, db_max_connections=100)
        report = system_load.load_report(now=self.now)
        self.assertEqual(report["warnings"], [])

    def test_warnings_in_plain_words(self):
        snapshot(1, self.now, cpu_percent=95.0, memory_percent=93.0, disk_percent=90.0,
                 queue_default=500, db_connections=90, db_max_connections=100,
                 longest_query_seconds=120.0)
        venue = make_venue("Kava")
        BotInstance.objects.create(
            partner=venue, display_name="K", username="kava_load_bot", token_encrypted="",
            runtime_state=BotInstance.RuntimeState.TOKEN_REJECTED,
        )
        warnings = " ".join(system_load.load_report(now=self.now)["warnings"])
        for words in ("Процессор", "Память", "Диск", "очеред", "соединени",
                      "запрос", "бот"):
            self.assertIn(words, warnings)

    def test_no_snapshot_for_minutes_means_background_tasks_stopped(self):
        snapshot(6, self.now)
        report = system_load.load_report(now=self.now)
        self.assertIn("Фоновые задачи", " ".join(report["warnings"]))
        self.assertEqual(report["stale_minutes"], 6)

    def test_close_to_the_tested_capacity(self):
        snapshot(0, self.now)
        with mock.patch.object(system_load, "active_partners", return_value=30), \
                self.settings(PLATFORM_CAPACITY_PARTNERS=35):
            report = system_load.load_report(now=self.now)
        self.assertIn("30 из 35", " ".join(report["warnings"]))

    def test_charts_for_a_day_and_a_week(self):
        for minutes in range(0, 3 * 24 * 60, 30):
            snapshot(minutes, self.now, http_requests=6, bot_updates=60)
        day = system_load.load_report(now=self.now, period="day")
        week = system_load.load_report(now=self.now, period="week")
        self.assertEqual(len(day["labels"]), 144)  # every 10 minutes
        self.assertEqual(len(week["labels"]), 168)  # every hour
        titles = [str(chart["title"]) for chart in day["charts"]]
        self.assertEqual(len(titles), 4)
        self.assertIn('"datasets"', day["charts"][0]["data"])

    def test_no_snapshots_yet(self):
        report = system_load.load_report(now=self.now)
        self.assertIsNone(report["latest"])
        self.assertIsNone(report["stale_minutes"])


class PageTests(TestCase):
    def test_the_platform_sees_the_page_in_its_menu(self):
        root = User.objects.create_superuser(username="load_root", password="x", email="",
                                             language="ru")
        self.client.force_login(root)
        snapshot(0, timezone.now())
        url = reverse("admin:analytics_systemsnapshot_changelist")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Нагрузка")
        self.assertContains(response, 'class="chart"')
        self.assertContains(response, 'http-equiv="refresh"')
        self.assertContains(self.client.get(url + "?period=week"), "Неделя")
        self.assertContains(self.client.get(reverse("admin:index")), url)

    def test_a_venue_does_not(self):
        venue = make_venue("Kava")
        owner = admin_user(venue, "load_owner", AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(owner)
        url = reverse("admin:analytics_systemsnapshot_changelist")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertNotContains(self.client.get(reverse("admin:index")), url)
