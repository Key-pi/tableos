"""Demo data used by Docker and for manual testing (`seed_baseline_demo`)."""

from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.bonuses.models import BonusTransaction
from apps.bonuses.services import get_bonus_ledger_balance
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuItem
from apps.partners.models import BotInstance, OpeningHours, Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.users.models import GuestProfile, TelegramAccount, User
from bot.services.runtime import load_active_bot_configs

CAFE_TOKEN = "111111111:AAEcafecafecafecafecafecafecafe"
HOOKAH_TOKEN = "222222222:AAEhookahhookahhookahhookahhook"
TESTER_ID = 555000111


def _seed(**options) -> str:
    out = StringIO()
    defaults = {
        "hookah_bot_token": "",
        "hookah_bot_username": "",
        "cafe_bot_token": "",
        "cafe_bot_username": "",
        "telegram_id": "",
        "telegram_username": "",
    }
    defaults.update(options)
    call_command("seed_baseline_demo", stdout=out, **defaults)
    return out.getvalue()


# The seed hashes a password for every demo user; a fast hasher keeps this quick.
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SeedBaselineDemoTests(TestCase):
    def test_fresh_seed_builds_both_venues_with_pickup_and_inactive_bots(self):
        output = _seed()

        hookah = Partner.objects.get(slug="night-owl-hookah")
        cafe = Partner.objects.get(slug="riverstone-cafe")
        for partner in (hookah, cafe):
            settings = PartnerBotSettings.objects.get(partner=partner)
            self.assertTrue(settings.module_pickup_enabled)
            self.assertTrue(settings.transfer_payment_details)
            bot = partner.bot_instances.get()
            self.assertFalse(bot.is_active)
            self.assertEqual(bot.token_status_label, "placeholder")
        self.assertEqual(OpeningHours.objects.filter(partner=hookah).count(), 7)
        self.assertFalse(OpeningHours.objects.filter(partner=cafe).exists())
        self.assertFalse(
            MenuItem.objects.get(partner=hookah, public_id="SMKHK001").available_for_pickup
        )
        self.assertTrue(
            MenuItem.objects.get(partner=cafe, public_id="CAFBR001").available_for_pickup
        )
        self.assertTrue(User.objects.get(username="platform_admin").is_superuser)
        self.assertIn("DEMO_CAFE_BOT_TOKEN", output)
        self.assertIn("DEMO_TELEGRAM_ID", output)
        # No bot is runnable yet, so `runbot` has nothing to start.
        self.assertEqual(load_active_bot_configs(), [])

    def test_rerun_is_idempotent(self):
        _seed()
        _seed()
        cafe = Partner.objects.get(slug="riverstone-cafe")
        self.assertEqual(Table.objects.filter(partner=cafe).count(), 12)
        self.assertEqual(EmployeeProfile.objects.filter(partner=cafe).count(), 8)
        self.assertEqual(BotInstance.objects.filter(partner=cafe).count(), 1)

    def test_tokens_and_tester_make_everything_testable(self):
        output = _seed(
            cafe_bot_token=CAFE_TOKEN,
            cafe_bot_username="@my_cafe_test_bot",
            hookah_bot_token=HOOKAH_TOKEN,
            hookah_bot_username="my_hookah_test_bot",
            telegram_id=str(TESTER_ID),
            telegram_username="@tester",
        )

        configs = load_active_bot_configs()
        self.assertEqual(
            sorted(config.username for config in configs),
            ["my_cafe_test_bot", "my_hookah_test_bot"],
        )
        tester = TelegramAccount.objects.get(telegram_id=TESTER_ID)
        self.assertEqual(tester.username, "tester")
        for slug, owner in (
            ("riverstone-cafe", "cafe_owner_1"),
            ("night-owl-hookah", "smoke_owner_1"),
        ):
            employee = EmployeeProfile.objects.get(partner__slug=slug, user__username=owner)
            self.assertEqual(employee.telegram_account, tester)
            self.assertTrue(employee.notify_on_order_created)
            guest = GuestProfile.objects.get(partner__slug=slug, telegram_account=tester)
            self.assertEqual(guest.loyalty_balance, Decimal("500.00"))
            self.assertEqual(
                get_bonus_ledger_balance(partner_id=guest.partner_id, guest_id=guest.id),
                Decimal("500.00"),
            )
        self.assertIn("https://t.me/my_cafe_test_bot?start=table_1_river_tbl_01", output)

        # Rerun: no second credit, binding kept.
        _seed(
            cafe_bot_token=CAFE_TOKEN,
            cafe_bot_username="my_cafe_test_bot",
            telegram_id=str(TESTER_ID),
        )
        self.assertEqual(BonusTransaction.objects.filter(guest__telegram_account=tester).count(), 2)
        self.assertEqual(
            EmployeeProfile.objects.get(
                partner__slug="riverstone-cafe", user__username="cafe_owner_1"
            ).telegram_account,
            tester,
        )
        # A real token is not dropped when it is no longer passed.
        self.assertTrue(BotInstance.objects.get(username="my_hookah_test_bot").is_active)

    def test_bot_username_is_looked_up_when_not_given(self):
        with patch(
            "apps.partners.management.commands.seed_baseline_demo.fetch_bot_username",
            return_value="looked_up_bot",
        ) as lookup:
            _seed(cafe_bot_token=CAFE_TOKEN)
        lookup.assert_called_once_with(CAFE_TOKEN)
        self.assertTrue(BotInstance.objects.get(username="looked_up_bot").is_active)

    def test_keep_existing_preserves_admin_changes_but_applies_bots(self):
        _seed()
        item = MenuItem.objects.get(public_id="CAFBR001")
        item.price = Decimal("999.00")
        item.save()
        admin = User.objects.get(username="platform_admin")
        admin.set_password("changed-in-admin")
        admin.save()

        _seed(keep_existing=True, cafe_bot_token=CAFE_TOKEN, cafe_bot_username="kept_cafe_bot")

        item.refresh_from_db()
        self.assertEqual(item.price, Decimal("999.00"))
        admin.refresh_from_db()
        self.assertTrue(admin.check_password("changed-in-admin"))
        self.assertTrue(BotInstance.objects.get(username="kept_cafe_bot").is_active)

    def test_keep_existing_on_empty_database_seeds_everything(self):
        _seed(keep_existing=True)
        self.assertEqual(
            Partner.objects.filter(slug__in=["riverstone-cafe", "night-owl-hookah"]).count(), 2
        )
        self.assertTrue(MenuItem.objects.filter(public_id="CAFBR001").exists())

    def test_same_token_for_both_venues_is_refused_before_any_change(self):
        with self.assertRaisesMessage(CommandError, "Один и тот же токен"):
            _seed(
                cafe_bot_token=CAFE_TOKEN,
                cafe_bot_username="a_cafe_bot",
                hookah_bot_token=CAFE_TOKEN,
                hookah_bot_username="a_hookah_bot",
            )
        self.assertFalse(Partner.objects.exists())

    def test_invalid_telegram_id_is_refused(self):
        with self.assertRaisesMessage(CommandError, "DEMO_TELEGRAM_ID"):
            _seed(telegram_id="@tester")

    def test_tester_already_bound_to_another_employee_keeps_that_binding(self):
        _seed()
        tester = TelegramAccount.objects.create(telegram_id=TESTER_ID)
        waiter = EmployeeProfile.objects.get(user__username="cafe_waiter_1")
        waiter.telegram_account = tester
        waiter.save()

        output = _seed(keep_existing=True, telegram_id=str(TESTER_ID))

        waiter.refresh_from_db()
        self.assertEqual(waiter.telegram_account, tester)
        self.assertNotEqual(
            EmployeeProfile.objects.get(user__username="cafe_owner_1").telegram_account, tester
        )
        self.assertIn("cafe_waiter_1", output)
