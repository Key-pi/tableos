from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.notifications.models import BroadcastCampaign, NotificationPreference, StaffNotification
from apps.notifications.services import (
    deliver_staff_notification,
    request_staff_assistance,
    send_broadcast_campaign,
)
from apps.orders.services import create_order_from_session
from apps.partners.models import BotInstance, Partner
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import GuestProfile, TelegramAccount, User


async def _successful_send(_token, messages):
    return [(chat_id, True, "") for chat_id, _text in messages]


async def _partially_failed_send(_token, messages):
    results = []
    for index, (chat_id, _text) in enumerate(messages):
        if index == 0:
            results.append((chat_id, True, ""))
        else:
            results.append((chat_id, False, "blocked"))
    return results


class NotificationDeliveryTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Notify Partner",
            slug="notify-partner",
            status=Partner.Status.ACTIVE,
        )
        self.bot = BotInstance.objects.create(
            partner=self.partner,
            display_name="Notify Bot",
            username="notify_partner_bot",
            mode=BotInstance.Mode.POLLING,
            is_active=True,
            token_encrypted="",
        )
        self.bot.set_token("123456789:ABCDEFGHIJKLMNOPQRST_uv-wxyz")
        self.bot.save(update_fields=["token_encrypted"])

    def test_deliver_staff_notification_runs_after_commit_and_marks_sent(self):
        user = User.objects.create_user(
            username="notify_staff",
            password="pass12345",
            partner=self.partner,
            role=User.Role.MANAGER,
        )
        account = TelegramAccount.objects.create(
            telegram_id=501001,
            username="notify_staff",
        )
        employee = EmployeeProfile.objects.create(
            partner=self.partner,
            user=user,
            telegram_account=account,
            bot_notifications_enabled=True,
        )
        notification = StaffNotification.objects.create(
            partner=self.partner,
            employee=employee,
            category=StaffNotification.Category.ORDER_CREATED,
            title="New order",
            message="Order arrived",
        )

        with patch("apps.notifications.services._send_telegram_messages", new=_successful_send):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                deliver_staff_notification(notification)

        notification.refresh_from_db()
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(notification.delivery_status, StaffNotification.DeliveryStatus.SENT)
        self.assertIsNotNone(notification.delivered_at)

    def test_send_broadcast_campaign_runs_after_commit_and_updates_delivery_counters(self):
        guest_account_a = TelegramAccount.objects.create(
            telegram_id=601001,
            username="guest_a",
        )
        guest_account_b = TelegramAccount.objects.create(
            telegram_id=601002,
            username="guest_b",
        )
        guest_a = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=guest_account_a,
        )
        guest_b = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=guest_account_b,
        )
        NotificationPreference.objects.create(
            partner=self.partner,
            guest=guest_a,
            marketing_enabled=True,
        )
        NotificationPreference.objects.create(
            partner=self.partner,
            guest=guest_b,
            marketing_enabled=True,
        )
        campaign = BroadcastCampaign.objects.create(
            partner=self.partner,
            name="Promo",
            message="Hello guests",
        )

        with patch(
            "apps.notifications.services._send_telegram_messages",
            new=_partially_failed_send,
        ):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                send_broadcast_campaign(campaign)

        campaign.refresh_from_db()
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(campaign.delivered_count, 1)
        self.assertEqual(campaign.failed_count, 1)
        self.assertEqual(campaign.status, BroadcastCampaign.Status.SENT)


class StaffNotificationCreationTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Orders Notify",
            slug="orders-notify",
            status=Partner.Status.ACTIVE,
        )
        self.bot = BotInstance.objects.create(
            partner=self.partner,
            display_name="Orders Bot",
            username="orders_notify_bot",
            mode=BotInstance.Mode.POLLING,
            is_active=True,
            token_encrypted="",
        )
        self.bot.set_token("223456789:ABCDEFGHIJKLMNOPQRST_uv-wxyz")
        self.bot.save(update_fields=["token_encrypted"])
        self.table = Table.objects.create(
            partner=self.partner,
            number=1,
            name="Table 1",
            qr_token="orders_notify_01",
        )
        category = MenuCategory.objects.create(
            partner=self.partner,
            name="Drinks",
            sort_order=10,
        )
        self.menu_item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="ORDN0001",
            name="Tea",
            price="120.00",
            sort_order=10,
        )
        self.session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=701001,
            payload=self.table.deep_link_payload,
            username="guest_notify",
            first_name="Guest",
        )
        user = User.objects.create_user(
            username="orders_manager",
            password="pass12345",
            partner=self.partner,
            role=User.Role.MANAGER,
        )
        account = TelegramAccount.objects.create(
            telegram_id=701101,
            username="orders_manager",
        )
        self.employee = EmployeeProfile.objects.create(
            partner=self.partner,
            user=user,
            telegram_account=account,
            bot_notifications_enabled=True,
            notify_on_order_created=True,
        )

    def test_create_order_queues_delivery(self):
        with patch("apps.notifications.services._send_telegram_messages", new=_successful_send):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                order = create_order_from_session(
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

        notification = order.staff_notifications.get(employee=self.employee)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(notification.delivery_status, StaffNotification.DeliveryStatus.SENT)

    def test_request_staff_assistance_notifies_waiter_and_manager_roles(self):
        waiter_user = User.objects.create_user(
            username="orders_waiter",
            password="pass12345",
            partner=self.partner,
            role=User.Role.WAITER,
        )
        waiter_account = TelegramAccount.objects.create(
            telegram_id=701102,
            username="orders_waiter",
        )
        waiter = EmployeeProfile.objects.create(
            partner=self.partner,
            user=waiter_user,
            telegram_account=waiter_account,
            bot_notifications_enabled=True,
            notify_on_guest_calls=True,
        )
        cashier_user = User.objects.create_user(
            username="orders_cashier",
            password="pass12345",
            partner=self.partner,
            role=User.Role.CASHIER,
        )
        cashier_account = TelegramAccount.objects.create(
            telegram_id=701103,
            username="orders_cashier",
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=cashier_user,
            telegram_account=cashier_account,
            bot_notifications_enabled=True,
            notify_on_guest_calls=False,
        )

        with patch("apps.notifications.services._send_telegram_messages", new=_successful_send):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                table_number, queued_count = request_staff_assistance(
                    partner_id=self.partner.id,
                    telegram_id=701001,
                    call_target="waiter",
                    call_target_label="Официант",
                )

        self.assertEqual(table_number, 1)
        self.assertEqual(queued_count, 2)
        self.assertEqual(len(callbacks), 2)
        notified_employee_ids = set(
            StaffNotification.objects.filter(
                partner=self.partner,
                category=StaffNotification.Category.GUEST_CALL,
            ).values_list("employee_id", flat=True)
        )
        self.assertSetEqual(
            notified_employee_ids,
            {self.employee.id, waiter.id},
        )
        self.assertEqual(
            StaffNotification.objects.filter(
                partner=self.partner,
                category=StaffNotification.Category.GUEST_CALL,
                delivery_status=StaffNotification.DeliveryStatus.SENT,
            ).count(),
            2,
        )
