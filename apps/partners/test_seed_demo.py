"""AA-019: the demo seed is atomic and idempotent."""

from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuItem
from apps.partners.models import Partner
from apps.tables.models import Table
from apps.users.models import TelegramAccount, User


def _seed(**options):
    call_command("seed_demo", stdout=StringIO(), **options)


class SeedDemoTests(TestCase):
    def test_rerun_is_idempotent(self):
        options = {"with_manager": True, "manager_telegram_id": 770001}
        _seed(**options)
        _seed(**options)

        partner = Partner.objects.get(slug="demo-lounge")
        self.assertEqual(Table.objects.filter(partner=partner).count(), 5)
        self.assertEqual(MenuItem.objects.filter(partner=partner).count(), 4)
        self.assertEqual(EmployeeProfile.objects.filter(partner=partner).count(), 1)

    def test_conflict_rolls_back_every_change(self):
        other = Partner.objects.create(name="Other", slug="other-venue")
        User.objects.create_user(username="demo_manager", password="x", partner=other)
        account = TelegramAccount.objects.create(telegram_id=770002, username="before")

        with self.assertRaisesMessage(CommandError, "already belongs to another venue"):
            _seed(
                with_manager=True,
                manager_telegram_id=770002,
                manager_telegram_username="after",
            )

        account.refresh_from_db()
        self.assertEqual(account.username, "before")
        self.assertFalse(Partner.objects.filter(slug="demo-lounge").exists())
        self.assertEqual(User.objects.get(username="demo_manager").partner, other)

    def test_venue_user_is_not_promoted_to_platform_admin(self):
        other = Partner.objects.create(name="Other", slug="other-venue")
        User.objects.create_user(username="admin", password="x", partner=other)

        with self.assertRaisesMessage(CommandError, "refusing to make it a platform admin"):
            _seed(with_admin=True)

        self.assertFalse(User.objects.get(username="admin").is_superuser)
        self.assertFalse(Partner.objects.filter(slug="demo-lounge").exists())
