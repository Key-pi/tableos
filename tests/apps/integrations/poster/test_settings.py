"""The Poster application's ID, secret and return address come only from the
environment (ADR-0010, PQ-058 step 1)."""

import os
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from core.settings.env import AppSettings


class PosterSettingsTests(SimpleTestCase):
    def test_values_are_read_from_the_environment(self):
        environment = {
            "POSTER_APPLICATION_ID": "5611",
            "POSTER_APPLICATION_SECRET": "secret-placeholder",
            "POSTER_REDIRECT_URI": "https://shop.example.com/integrations/poster/oauth/callback/",
        }
        with patch.dict(os.environ, environment):
            values = AppSettings(_env_file=None)

        self.assertEqual(values.poster_application_id, "5611")
        self.assertEqual(values.poster_application_secret, "secret-placeholder")
        self.assertEqual(
            values.poster_redirect_uri,
            "https://shop.example.com/integrations/poster/oauth/callback/",
        )

    def test_without_them_poster_is_simply_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            values = AppSettings(_env_file=None)

        self.assertEqual(values.poster_application_id, "")
        self.assertEqual(values.poster_application_secret, "")
        self.assertEqual(values.poster_redirect_uri, "")
        for name in ("POSTER_APPLICATION_ID", "POSTER_APPLICATION_SECRET", "POSTER_REDIRECT_URI"):
            self.assertTrue(hasattr(settings, name), name)
