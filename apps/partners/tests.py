from django.test import TestCase

from apps.partners.forms import BotInstanceAdminForm
from apps.partners.models import BotInstance, Partner
from bot.services.runtime import BotRuntimeConfigError, load_active_bot_configs


class BotInstanceAdminFormTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Test Venue",
            slug="test-venue",
            status=Partner.Status.ACTIVE,
        )

    def test_cannot_activate_seed_placeholder_without_real_token(self):
        bot_instance = BotInstance.objects.create(
            partner=self.partner,
            display_name="Seed Bot",
            username="seed_test_bot",
            mode=BotInstance.Mode.POLLING,
            is_active=False,
            token_encrypted="",
        )
        bot_instance.set_token("seed-placeholder-token-test-venue")
        bot_instance.save(update_fields=["token_encrypted"])

        form = BotInstanceAdminForm(
            data={
                "partner": str(self.partner.id),
                "display_name": bot_instance.display_name,
                "username": bot_instance.username,
                "raw_token": "",
                "mode": BotInstance.Mode.POLLING,
                "webhook_url": "",
                "is_active": "on",
            },
            instance=bot_instance,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("raw_token", form.errors)

    def test_accepts_real_token_and_normalizes_username(self):
        bot_instance = BotInstance(
            partner=self.partner,
            display_name="Real Bot",
            username="real_test_bot",
            token_encrypted="",
        )
        form = BotInstanceAdminForm(
            data={
                "partner": str(self.partner.id),
                "display_name": "Real Bot",
                "username": "@RealTestBot",
                "raw_token": "123456789:ABCDEFGHIJKLMNOPQRST_uv-wxyz",
                "mode": BotInstance.Mode.POLLING,
                "webhook_url": "",
                "is_active": "on",
            },
            instance=bot_instance,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved_instance = form.save()
        self.assertEqual(saved_instance.username, "RealTestBot")
        self.assertTrue(saved_instance.has_usable_token)


class BotRuntimeConfigTests(TestCase):
    def _create_bot(
        self,
        *,
        partner: Partner,
        username: str,
        token: str,
        is_active: bool = True,
        mode: str = BotInstance.Mode.POLLING,
    ) -> BotInstance:
        bot_instance = BotInstance.objects.create(
            partner=partner,
            display_name=username,
            username=username,
            mode=mode,
            is_active=is_active,
            token_encrypted="",
        )
        bot_instance.set_token(token)
        bot_instance.save(update_fields=["token_encrypted"])
        return bot_instance

    def test_rejects_duplicate_active_tokens(self):
        partner_a = Partner.objects.create(
            name="Venue A",
            slug="venue-a",
            status=Partner.Status.ACTIVE,
        )
        partner_b = Partner.objects.create(
            name="Venue B",
            slug="venue-b",
            status=Partner.Status.ACTIVE,
        )
        self._create_bot(
            partner=partner_a,
            username="venue_a_bot",
            token="123456789:ABCDEFGHIJKLMNOPQRST_uv-wxyz",
        )
        self._create_bot(
            partner=partner_b,
            username="venue_b_bot",
            token="123456789:ABCDEFGHIJKLMNOPQRST_uv-wxyz",
        )

        with self.assertRaises(BotRuntimeConfigError):
            load_active_bot_configs()

    def test_rejects_active_webhook_bots_for_runbot(self):
        partner = Partner.objects.create(
            name="Webhook Venue",
            slug="webhook-venue",
            status=Partner.Status.ACTIVE,
        )
        self._create_bot(
            partner=partner,
            username="webhook_test_bot",
            token="123456789:ABCDEFGHIJKLMNOPQRST_uv-wxyz",
            mode=BotInstance.Mode.WEBHOOK,
        )

        with self.assertRaises(BotRuntimeConfigError):
            load_active_bot_configs()

    def test_rejects_active_bots_of_non_active_partners(self):
        active_partner = Partner.objects.create(
            name="Active Venue",
            slug="active-venue",
            status=Partner.Status.ACTIVE,
        )
        suspended_partner = Partner.objects.create(
            name="Suspended Venue",
            slug="suspended-venue",
            status=Partner.Status.SUSPENDED,
        )
        self._create_bot(
            partner=active_partner,
            username="active_venue_bot",
            token="123456789:ABCDEFGHIJKLMNOPQRST_uv-wxyz",
        )
        self._create_bot(
            partner=suspended_partner,
            username="suspended_venue_bot",
            token="987654321:ABCDEFGHIJKLMNOPQRST_uv-wxyz",
        )

        with self.assertRaises(BotRuntimeConfigError):
            load_active_bot_configs()

    def test_loads_valid_polling_config(self):
        partner = Partner.objects.create(
            name="Polling Venue",
            slug="polling-venue",
            status=Partner.Status.ACTIVE,
        )
        bot_instance = self._create_bot(
            partner=partner,
            username="polling_venue_bot",
            token="123456789:ABCDEFGHIJKLMNOPQRST_uv-wxyz",
        )

        configs = load_active_bot_configs()

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].username, bot_instance.username)
        self.assertEqual(configs[0].partner_id, str(partner.id))
