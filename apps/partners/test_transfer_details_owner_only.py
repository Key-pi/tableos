"""Transfer details: only the owner sees and changes them, and is told (AA-035)."""

from unittest.mock import patch

from django.contrib import admin
from django.forms.models import model_to_dict
from django.test import RequestFactory, TestCase

from apps.employees.models import EmployeeProfile
from apps.notifications.models import StaffNotification
from apps.partners.admin import PartnerBotSettingsAdmin
from apps.partners.models import Partner, PartnerBotSettings
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, TelegramAccount, User


class TransferDetailsOwnerOnlyTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(name="P", slug="p", status=Partner.Status.ACTIVE)
        self.settings = PartnerBotSettings.objects.create(
            partner=self.partner, transfer_payment_details="Карта 4149 0000 0000 0001"
        )
        self.owner = User.objects.create_user(
            username="own", password="x", partner=self.partner, role=User.Role.OWNER
        )
        AdminAccessProfile.objects.create(
            user=self.owner,
            partner=self.partner,
            preset=AdminAccessPreset.OWNER,
            is_partner_owner=True,
            can_access_admin=True,
        )
        self.owner_employee = EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.owner,
            telegram_account=TelegramAccount.objects.create(telegram_id=8100001),
            # Security alerts ignore the per-topic switches.
            bot_notifications_enabled=False,
        )
        self.manager = User.objects.create_user(
            username="man", password="x", partner=self.partner, role=User.Role.MANAGER
        )
        AdminAccessProfile.objects.create(
            user=self.manager,
            partner=self.partner,
            preset=AdminAccessPreset.MANAGER,
            can_access_admin=True,
        )
        self.model_admin = PartnerBotSettingsAdmin(PartnerBotSettings, admin.site)

    def _request(self, user):
        request = RequestFactory().post("/admin/")
        request.user = user
        return request

    def _save(self, user, **changes):
        request = self._request(user)
        form_class = self.model_admin.get_form(request, self.settings, change=True)
        data = {
            name: value
            for name, value in model_to_dict(self.settings).items()
            if name in form_class.base_fields and value is not None and value is not False
        }
        data.update(changes)
        form = form_class(data=data, instance=self.settings)
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        with self.captureOnCommitCallbacks(execute=True), patch(
            "apps.notifications.services.deliver_staff_notification"
        ):
            self.model_admin.save_model(request, obj, form, change=True)
        self.settings.refresh_from_db()

    def test_manager_does_not_see_or_change_transfer_details(self):
        request = self._request(self.manager)
        form_class = self.model_admin.get_form(request, self.settings, change=True)
        self.assertNotIn("transfer_payment_details", form_class.base_fields)
        self._save(self.manager, duplicate_request_cooldown_seconds=90)
        self.assertEqual(self.settings.transfer_payment_details, "Карта 4149 0000 0000 0001")
        self.assertEqual(self.settings.duplicate_request_cooldown_seconds, 90)

    def test_owner_changes_details_and_gets_a_security_message(self):
        self._save(self.owner, transfer_payment_details="Карта 5168 0000 0000 0002")
        self.assertEqual(self.settings.transfer_payment_details, "Карта 5168 0000 0000 0002")
        alert = StaffNotification.objects.get(employee=self.owner_employee)
        self.assertEqual(alert.category, StaffNotification.Category.SECURITY)
        self.assertIn("Карта 5168 0000 0000 0002", alert.message)
        self.assertIn("own", alert.message)

    def test_no_message_when_details_did_not_change(self):
        self._save(self.owner, duplicate_request_cooldown_seconds=90)
        self.assertFalse(StaffNotification.objects.exists())

    def test_alert_is_sent_even_with_bot_notifications_off(self):
        from apps.notifications.services import deliver_staff_notification_now
        from apps.partners.models import BotInstance
        from core.security.crypto import encrypt_text

        BotInstance.objects.create(
            partner=self.partner,
            display_name="Bot",
            username="p_bot",
            token_encrypted=encrypt_text("123456789:AAEexampleexampleexampleexample"),
        )
        self._save(self.owner, transfer_payment_details="Карта 5168 0000 0000 0003")
        alert = StaffNotification.objects.get()
        sent = []

        async def capture(_token, messages, **_kwargs):
            sent.extend(messages)
            return [(message[0], True, "", False, False) for message in messages]

        with patch("apps.notifications.services._send_telegram_messages", new=capture):
            alert = deliver_staff_notification_now(alert)
        self.assertEqual(alert.delivery_status, StaffNotification.DeliveryStatus.SENT)
        self.assertEqual(sent[0][0], 8100001)
