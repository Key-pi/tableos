from __future__ import annotations

from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.notifications.models import BroadcastCampaign, NotificationPreference, StaffNotification
from apps.notifications.services import (
    deliver_staff_notification,
    process_scheduled_broadcast_campaigns,
    request_staff_assistance,
    send_broadcast_campaign,
    send_broadcast_campaign_now,
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


async def _blocked_send(_token, messages):
    return [(chat_id, False, "bot was blocked by the user", True) for chat_id, _text in messages]


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

    def test_process_scheduled_broadcast_campaigns_queues_due_campaign(self):
        guest_account = TelegramAccount.objects.create(
            telegram_id=601010,
            username="guest_due",
        )
        guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=guest_account,
        )
        NotificationPreference.objects.create(
            partner=self.partner,
            guest=guest,
            marketing_enabled=True,
        )
        campaign = BroadcastCampaign.objects.create(
            partner=self.partner,
            name="Due promo",
            message="Scheduled hello",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )

        with patch(
            "apps.notifications.services._send_telegram_messages",
            new=_successful_send,
        ):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                processed_count = process_scheduled_broadcast_campaigns()

        campaign.refresh_from_db()
        self.assertEqual(processed_count, 1)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(campaign.status, BroadcastCampaign.Status.SENT)
        self.assertEqual(campaign.delivered_count, 1)

    def test_process_scheduled_broadcast_campaigns_skips_future_campaign(self):
        campaign = BroadcastCampaign.objects.create(
            partner=self.partner,
            name="Future promo",
            message="Scheduled later",
            scheduled_at=timezone.now() + timedelta(minutes=10),
        )

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            processed_count = process_scheduled_broadcast_campaigns()

        campaign.refresh_from_db()
        self.assertEqual(processed_count, 0)
        self.assertEqual(len(callbacks), 0)
        self.assertEqual(campaign.status, BroadcastCampaign.Status.DRAFT)

    def test_broadcast_delivery_is_processed_in_batches(self):
        guests = []
        for index in range(3):
            account = TelegramAccount.objects.create(
                telegram_id=601100 + index,
                username=f"batch_guest_{index}",
            )
            guest = GuestProfile.objects.create(
                partner=self.partner,
                telegram_account=account,
            )
            NotificationPreference.objects.create(
                partner=self.partner,
                guest=guest,
                marketing_enabled=True,
            )
            guests.append(guest)
        campaign = BroadcastCampaign.objects.create(
            partner=self.partner,
            name="Batch promo",
            message="Hello batch",
        )

        with patch("apps.notifications.services.BROADCAST_BATCH_SIZE", 2):
            with patch(
                "apps.notifications.services._send_telegram_messages",
                new=_successful_send,
            ):
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    send_broadcast_campaign_now(campaign)

                campaign.refresh_from_db()
                self.assertEqual(campaign.status, BroadcastCampaign.Status.SENDING)
                self.assertEqual(campaign.delivered_count, 2)
                self.assertEqual(len(callbacks), 1)

                for callback in callbacks:
                    callback()

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, BroadcastCampaign.Status.SENT)
        self.assertEqual(campaign.delivered_count, 3)
        self.assertEqual(campaign.failed_count, 0)

    def test_broadcast_delivery_marks_blocked_accounts(self):
        guest_account = TelegramAccount.objects.create(
            telegram_id=601030,
            username="blocked_guest",
        )
        guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=guest_account,
        )
        NotificationPreference.objects.create(
            partner=self.partner,
            guest=guest,
            marketing_enabled=True,
        )
        campaign = BroadcastCampaign.objects.create(
            partner=self.partner,
            name="Blocked promo",
            message="Will be blocked",
        )

        with patch(
            "apps.notifications.services._send_telegram_messages",
            new=_blocked_send,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                send_broadcast_campaign(campaign)

        campaign.refresh_from_db()
        guest_account.refresh_from_db()
        self.assertTrue(guest_account.is_blocked)
        self.assertEqual(campaign.status, BroadcastCampaign.Status.FAILED)
        self.assertEqual(campaign.failed_count, 1)

    def test_send_broadcast_campaign_fails_when_audience_is_empty(self):
        campaign = BroadcastCampaign.objects.create(
            partner=self.partner,
            name="Empty promo",
            message="Nobody will receive this",
        )

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            send_broadcast_campaign(campaign)

        campaign.refresh_from_db()
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(campaign.status, BroadcastCampaign.Status.FAILED)
        self.assertEqual(campaign.delivered_count, 0)
        self.assertEqual(campaign.failed_count, 0)
        self.assertEqual(campaign.last_error, "No eligible recipients for this campaign.")

    def test_send_broadcast_campaign_fails_for_suspended_partner(self):
        guest_account = TelegramAccount.objects.create(
            telegram_id=601020,
            username="guest_suspended",
        )
        guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=guest_account,
        )
        NotificationPreference.objects.create(
            partner=self.partner,
            guest=guest,
            marketing_enabled=True,
        )
        self.partner.status = Partner.Status.SUSPENDED
        self.partner.save(update_fields=["status", "updated_at"])
        campaign = BroadcastCampaign.objects.create(
            partner=self.partner,
            name="Suspended promo",
            message="Should not be delivered",
        )

        with patch("apps.notifications.services._send_telegram_messages", new=_successful_send):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                send_broadcast_campaign(campaign)

        campaign.refresh_from_db()
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(campaign.status, BroadcastCampaign.Status.FAILED)
        self.assertEqual(
            campaign.last_error,
            "Partner is not active, delivery is disabled.",
        )


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

    def test_request_staff_assistance_excludes_inactive_django_users(self):
        self.employee.user.is_active = False
        self.employee.user.save(update_fields=["is_active"])

        waiter_user = User.objects.create_user(
            username="active_orders_waiter",
            password="pass12345",
            partner=self.partner,
            role=User.Role.WAITER,
        )
        waiter_account = TelegramAccount.objects.create(
            telegram_id=701104,
            username="active_orders_waiter",
        )
        waiter = EmployeeProfile.objects.create(
            partner=self.partner,
            user=waiter_user,
            telegram_account=waiter_account,
            bot_notifications_enabled=True,
            notify_on_guest_calls=True,
        )

        with patch("apps.notifications.services._send_telegram_messages", new=_successful_send):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                _table_number, queued_count = request_staff_assistance(
                    partner_id=self.partner.id,
                    telegram_id=701001,
                    call_target="waiter",
                    call_target_label="Официант",
                )

        self.assertEqual(queued_count, 1)
        self.assertEqual(len(callbacks), 1)
        self.assertSetEqual(
            set(
                StaffNotification.objects.filter(
                    partner=self.partner,
                    category=StaffNotification.Category.GUEST_CALL,
                ).values_list("employee_id", flat=True)
            ),
            {waiter.id},
        )
