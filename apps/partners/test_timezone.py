"""AA-034: venues use the IANA name Europe/Kyiv and reject unknown zones."""

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from apps.partners.models import Partner
from core.settings.env import AppSettings

BEFORE = [("partners", "0005_partnerbotsettings_button_profile_bonuses_label")]
AFTER = [("partners", "0006_partner_timezone_kyiv")]


class PartnerTimezoneTests(TestCase):
    def test_default_is_the_current_iana_name(self):
        self.assertEqual(Partner().timezone, "Europe/Kyiv")
        self.assertEqual(AppSettings.model_fields["timezone"].default, "Europe/Kyiv")

    def test_legacy_env_value_is_read_as_kyiv(self):
        self.assertEqual(AppSettings(DJANGO_TIMEZONE="Europe/Kiev").timezone, "Europe/Kyiv")

    def test_unknown_timezone_is_rejected(self):
        partner = Partner(name="Tz", slug="tz-venue", timezone="Mars/Olympus")
        with self.assertRaises(ValidationError) as caught:
            partner.full_clean()
        self.assertIn("timezone", caught.exception.message_dict)

    def test_known_timezone_is_accepted(self):
        Partner(name="Tz", slug="tz-venue-ok", timezone="Europe/Warsaw").full_clean()


class PartnerTimezoneMigrationTests(TransactionTestCase):
    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_legacy_kiev_is_renamed(self):
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        old_partner = executor.loader.project_state(BEFORE).apps.get_model("partners", "Partner")
        legacy = old_partner.objects.create(name="Old", slug="old", timezone="Europe/Kiev")
        other = old_partner.objects.create(name="Other", slug="other", timezone="Europe/Warsaw")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(AFTER)

        new_partner = executor.loader.project_state(AFTER).apps.get_model("partners", "Partner")
        self.assertEqual(new_partner.objects.get(id=legacy.id).timezone, "Europe/Kyiv")
        self.assertEqual(new_partner.objects.get(id=other.id).timezone, "Europe/Warsaw")
