from django.test import TestCase

from apps.employees.forms import EmployeeProfileAdminForm
from apps.employees.models import EmployeeProfile
from apps.employees.selectors import get_staff_employee_by_telegram
from apps.partners.models import Partner
from apps.users.models import TelegramAccount, User


class EmployeeProfileAdminFormTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Employees Venue",
            slug="employees-venue",
            status=Partner.Status.ACTIVE,
        )
        self.user = User.objects.create_user(
            username="employee_user",
            password="pass12345",
            partner=self.partner,
            role=User.Role.WAITER,
        )
        self.superuser = User.objects.create_superuser(
            username="platform_admin",
            password="pass12345",
            email="platform@example.com",
        )

    def test_form_creates_telegram_binding_from_raw_fields(self):
        form = EmployeeProfileAdminForm(
            actor_user=self.superuser,
            data={
                "partner": str(self.partner.id),
                "user": str(self.user.id),
                "telegram_id": "8801001",
                "telegram_username": "@waiter_one",
                "title": "Waiter",
                "hourly_rate": "100.00",
                "commission_rate": "5.00",
                "bot_notifications_enabled": "on",
                "notify_on_order_created": "on",
                "notify_on_order_status_changed": "on",
                "notify_on_guest_calls": "on",
                "is_active": "on",
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        profile = form.save()
        self.assertIsInstance(profile, EmployeeProfile)
        self.assertEqual(profile.telegram_account.telegram_id, 8801001)
        self.assertEqual(profile.telegram_account.username, "waiter_one")

    def test_non_superuser_can_change_username_but_not_telegram_id(self):
        telegram_account = TelegramAccount.objects.create(
            telegram_id=8801002,
            username="original_identity",
        )
        profile = EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.user,
            telegram_account=telegram_account,
            title="Waiter",
        )
        form = EmployeeProfileAdminForm(
            actor_user=self.user,
            instance=profile,
            data={
                "partner": str(self.partner.id),
                "user": str(self.user.id),
                "telegram_id": "8801003",
                "telegram_username": "reassigned_identity",
                "title": "Senior waiter",
                "hourly_rate": "100.00",
                "commission_rate": "5.00",
                "bot_notifications_enabled": "on",
                "notify_on_order_created": "on",
                "notify_on_order_status_changed": "on",
                "notify_on_guest_calls": "on",
                "is_active": "on",
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        updated_profile = form.save()
        telegram_account.refresh_from_db()

        self.assertEqual(updated_profile.title, "Senior waiter")
        self.assertEqual(updated_profile.telegram_account_id, telegram_account.id)
        self.assertEqual(telegram_account.username, "reassigned_identity")
        self.assertFalse(TelegramAccount.objects.filter(telegram_id=8801003).exists())

    def test_non_superuser_can_clear_existing_telegram_username(self):
        telegram_account = TelegramAccount.objects.create(
            telegram_id=8801004,
            username="username_to_clear",
        )
        profile = EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.user,
            telegram_account=telegram_account,
            title="Waiter",
        )
        form = EmployeeProfileAdminForm(
            actor_user=self.user,
            instance=profile,
            data={
                "partner": str(self.partner.id),
                "user": str(self.user.id),
                "telegram_id": "8801004",
                "telegram_username": "",
                "title": "Waiter",
                "hourly_rate": "100.00",
                "commission_rate": "5.00",
                "bot_notifications_enabled": "on",
                "notify_on_order_created": "on",
                "notify_on_order_status_changed": "on",
                "notify_on_guest_calls": "on",
                "is_active": "on",
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        telegram_account.refresh_from_db()

        self.assertEqual(telegram_account.username, "")

    def test_form_requires_telegram_id_when_notifications_enabled(self):
        form = EmployeeProfileAdminForm(
            data={
                "partner": str(self.partner.id),
                "user": str(self.user.id),
                "telegram_id": "",
                "telegram_username": "",
                "title": "Waiter",
                "hourly_rate": "100.00",
                "commission_rate": "5.00",
                "bot_notifications_enabled": "on",
                "notify_on_order_created": "",
                "notify_on_order_status_changed": "",
                "notify_on_guest_calls": "",
                "is_active": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("telegram_id", form.errors)


class StaffEmployeeSelectorTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Staff Access Venue",
            slug="staff-access-venue",
            status=Partner.Status.ACTIVE,
        )
        self.user = User.objects.create_user(
            username="staff_access_user",
            password="pass12345",
            partner=self.partner,
            role=User.Role.WAITER,
        )
        self.telegram_account = TelegramAccount.objects.create(
            telegram_id=8810001,
            username="staff_access_user",
        )
        self.employee = EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.user,
            telegram_account=self.telegram_account,
            is_active=True,
        )

    def test_excludes_employee_when_django_user_is_inactive(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        with self.assertRaises(EmployeeProfile.DoesNotExist):
            get_staff_employee_by_telegram(
                self.partner.id,
                self.telegram_account.telegram_id,
            )

    def test_excludes_employee_when_user_partner_does_not_match_profile_partner(self):
        other_partner = Partner.objects.create(
            name="Other Staff Access Venue",
            slug="other-staff-access-venue",
            status=Partner.Status.ACTIVE,
        )
        self.user.partner = other_partner
        self.user.save(update_fields=["partner"])

        with self.assertRaises(EmployeeProfile.DoesNotExist):
            get_staff_employee_by_telegram(
                self.partner.id,
                self.telegram_account.telegram_id,
            )
