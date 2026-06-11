from django.test import TestCase

from apps.employees.forms import EmployeeProfileAdminForm
from apps.employees.models import EmployeeProfile
from apps.partners.models import Partner
from apps.users.models import User


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

    def test_form_creates_telegram_binding_from_raw_fields(self):
        form = EmployeeProfileAdminForm(
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
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        profile = form.save()
        self.assertIsInstance(profile, EmployeeProfile)
        self.assertEqual(profile.telegram_account.telegram_id, 8801001)
        self.assertEqual(profile.telegram_account.username, "waiter_one")

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
