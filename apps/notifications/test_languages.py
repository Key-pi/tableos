"""Notifications reach each person in their own language (PQ-040)."""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.employees.models import EmployeeProfile
from apps.notifications import tests as notification_tests
from apps.notifications.models import StaffNotification
from apps.notifications.services import (
    GuestCallError,
    notify_owners_about_transfer_details_change,
    request_staff_assistance,
    send_guest_order_status_update_now,
)
from apps.orders.models import Order
from apps.orders.services import create_order_from_session, transition_order_status
from apps.partners.models import PartnerBotSettings
from apps.users.models import GuestProfile, TelegramAccount, User
from bot.handlers import staff
from core.i18n import texts

SEND = "apps.notifications.services._send_telegram_messages"


class NotificationLanguageTests(TestCase):
    setUp = notification_tests.StaffNotificationCreationTests.setUp

    def _employee(self, username, telegram_id, language, role=User.Role.MANAGER, **flags):
        user = User.objects.create_user(
            username=username, password="x", partner=self.partner, role=role, language=language
        )
        return EmployeeProfile.objects.create(
            partner=self.partner,
            user=user,
            telegram_account=TelegramAccount.objects.create(telegram_id=telegram_id),
            bot_notifications_enabled=True,
            **flags,
        )

    def _order(self):
        with patch(SEND, new=notification_tests._successful_send):
            with self.captureOnCommitCallbacks(execute=True):
                return create_order_from_session(
                    partner_id=self.partner.id,
                    table_session=self.session,
                    items=[
                        {
                            "menu_item_id": self.menu_item.id,
                            "item_name": self.menu_item.name,
                            "unit_price": Decimal("120.00"),
                            "quantity": 1,
                        }
                    ],
                )

    def test_each_employee_reads_a_new_order_in_their_language(self):
        User.objects.filter(id=self.employee.user_id).update(language="en")
        ukrainian = self._employee("uk_staff", 701201, "uk", notify_on_order_created=True)
        order = self._order()
        titles = dict(
            StaffNotification.objects.filter(order=order).values_list("employee_id", "title")
        )
        self.assertEqual(titles[self.employee.id], f"New order #{order.public_id}")
        self.assertEqual(titles[ukrainian.id], f"Нове замовлення #{order.public_id}")
        message = StaffNotification.objects.get(order=order, employee=self.employee).message
        self.assertIn("table #1", message)

    def test_status_changes_use_status_names(self):
        english = self._employee("en_staff", 701202, "en", notify_on_order_status_changed=True)
        order = self._order()
        with patch(SEND, new=notification_tests._successful_send):
            with self.captureOnCommitCallbacks(execute=True):
                transition_order_status(order=order, to_status=Order.Status.ACCEPTED)
        notification = StaffNotification.objects.get(
            employee=english, category=StaffNotification.Category.ORDER_STATUS_CHANGED
        )
        self.assertEqual(notification.title, f"Order #{order.public_id}: Accepted")
        self.assertIn("from New to Accepted", notification.message)

    def test_a_staff_call_is_translated_and_not_repeated_in_another_language(self):
        User.objects.filter(id=self.employee.user_id).update(language="en")
        ukrainian = self._employee(
            "uk_waiter", 701203, "uk", role=User.Role.WAITER, notify_on_guest_calls=True
        )
        with patch(SEND, new=notification_tests._successful_send):
            with self.captureOnCommitCallbacks(execute=True):
                request_staff_assistance(
                    partner_id=self.partner.id,
                    telegram_id=701001,
                    call_target="waiter",
                    call_target_label="Официант",
                )
        titles = dict(
            StaffNotification.objects.filter(
                category=StaffNotification.Category.GUEST_CALL
            ).values_list("employee_id", "title")
        )
        self.assertEqual(titles[self.employee.id], "Call: Waiter")
        self.assertEqual(titles[ukrainian.id], "Виклик: Офіціант")
        # The same call pressed again in another language is still a repeat.
        with self.assertRaises(GuestCallError):
            request_staff_assistance(
                partner_id=self.partner.id,
                telegram_id=701001,
                call_target="waiter",
                call_target_label="Офіціант",
            )

    def test_the_guest_reads_status_updates_in_their_language(self):
        order = self._order()
        GuestProfile.objects.filter(id=order.guest_id).update(language="en")
        Order.objects.filter(id=order.id).update(status=Order.Status.ACCEPTED)
        sent = []

        async def capture(_token, messages, **_kwargs):
            sent.extend(messages)
            return [(message[0], True, "", False, False) for message in messages]

        with patch("apps.notifications.services._send_telegram_messages", new=capture):
            send_guest_order_status_update_now(
                order_id=str(order.id), from_status="new", to_status="accepted"
            )
        text = sent[0][1]
        self.assertIn(f"<b>Order #{order.public_id} update</b>", text)
        self.assertIn("New status: Accepted", text)

    def test_the_owner_reads_a_security_alert_in_their_language(self):
        owner = self._employee("owner_en", 701204, "en", role=User.Role.OWNER)
        settings = PartnerBotSettings.objects.create(
            partner=self.partner, transfer_payment_details="IBAN UA00"
        )
        with patch(SEND, new=notification_tests._successful_send):
            with self.captureOnCommitCallbacks(execute=True):
                notify_owners_about_transfer_details_change(settings=settings, actor=owner.user)
        alert = StaffNotification.objects.get(employee=owner)
        self.assertEqual(alert.title, "Payment details changed")
        self.assertIn("New details: IBAN UA00", alert.message)


class StaffBotTextTests(TestCase):
    def test_staff_texts_follow_the_language(self):
        expected = {
            "uk": "Відкритих замовлень зараз немає.",
            "ru": "Открытых заказов сейчас нет.",
            "en": "No open orders right now.",
        }
        for language, text in expected.items():
            with self.subTest(language=language), texts(language):
                self.assertEqual(staff._render_open_orders_summary([]), text)
