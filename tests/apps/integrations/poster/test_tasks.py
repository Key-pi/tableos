"""The menu from Poster at night (PQ-058, step 3): every working connection
of a venue that is not suspended; a refusal is remembered, not raised."""

from unittest import mock

from celery.schedules import crontab
from django.conf import settings
from django.test import TestCase

from apps.integrations.poster import menu_sync
from apps.integrations.poster.client import PosterError
from apps.integrations.poster.models import PosterConnection, PosterRef
from apps.integrations.poster.tasks import sync_all_menus_task, sync_menu_task
from apps.partners.models import Partner
from tests.apps.integrations.poster.test_menu_sync import connect, trial_menu


def venue(slug, status=Partner.Status.ACTIVE):
    return Partner.objects.create(name=slug, slug=slug, status=status)


class NightlySyncTests(TestCase):
    def setUp(self):
        self.fetch_mock = mock.patch.object(
            menu_sync, "fetch_menu", side_effect=lambda token: trial_menu(),
        ).start()
        self.addCleanup(mock.patch.stopall)

    def test_every_working_venue_and_only_those(self):
        working = venue("poster-night-working")
        connect(working)
        connect(venue("poster-night-off"), status=PosterConnection.Status.DISCONNECTED)
        connect(venue("poster-night-spot"), spot_id=None,
                status=PosterConnection.Status.PENDING_SPOT)
        connect(venue("poster-night-suspended", Partner.Status.SUSPENDED), spot_id=2)

        self.assertEqual(sync_all_menus_task(), 1)
        self.assertEqual(self.fetch_mock.call_count, 1)
        self.assertEqual(set(PosterRef.objects.values_list("partner_id", flat=True)),
                         {working.pk})

    def test_a_refusal_is_remembered(self):
        connection = connect(venue("poster-night-error"))
        self.fetch_mock.side_effect = PosterError("Poster answered HTTP 503", temporary=True)
        self.assertEqual(sync_menu_task(str(connection.pk)), "")
        connection.refresh_from_db()
        self.assertIn("HTTP 503", connection.last_error)

    def test_it_runs_at_night(self):
        entries = [entry for entry in settings.CELERY_BEAT_SCHEDULE.values()
                   if entry["task"] == "poster.sync_all_menus"]
        self.assertEqual(len(entries), 1)
        self.assertIsInstance(entries[0]["schedule"], crontab)
        self.assertEqual(entries[0]["schedule"].hour, {4})
