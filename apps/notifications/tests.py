from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin
from django.test import RequestFactory
from django.test import TestCase

from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.notifications.admin import BroadcastCampaignAdmin
from apps.notifications.forms import BroadcastCampaignAdminForm
from apps.notifications.models import BroadcastCampaign, NotificationPreference, StaffNotification
from apps.notifications.services import (
    deliver_staff_notification,
    get_broadcast_audience_stats,
    NotificationDeliveryError,
    process_scheduled_broadcast_campaigns,
    request_staff_assistance,
    send_broadcast_campaign,
)
from django.utils import timezone
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

    def test_send_broadcast_campaign_requires_eligible_recipients(self):
        campaign = BroadcastCampaign.objects.create(
            partner=self.partner,
            name="Promo",
            message="Hello guests",
        )

        with self.assertRaisesMessage(
            NotificationDeliveryError,
            "Нет гостей, которым можно отправить рассылку",
        ):
            send_broadcast_campaign(campaign)

    def test_process_scheduled_broadcast_campaigns_queues_due_campaign(self):
        guest_account = TelegramAccount.objects.create(
            telegram_id=601010,
            username="due_guest",
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
        due_campaign = BroadcastCampaign.objects.create(
            partner=self.partner,
            name="Due promo",
            message="Hello due guests",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        future_campaign = BroadcastCampaign.objects.create(
            partner=self.partner,
            name="Future promo",
            message="Hello future guests",
            scheduled_at=timezone.now() + timedelta(minutes=10),
        )

        with patch("apps.notifications.services._send_telegram_messages", new=_successful_send):
            with self.captureOnCommitCallbacks(execute=True):
                processed_count = process_scheduled_broadcast_campaigns()

        due_campaign.refresh_from_db()
        future_campaign.refresh_from_db()
        self.assertEqual(processed_count, 1)
        self.assertEqual(due_campaign.status, BroadcastCampaign.Status.SENT)
        self.assertEqual(future_campaign.status, BroadcastCampaign.Status.DRAFT)

    def test_process_scheduled_broadcast_campaigns_marks_invalid_campaign_failed(self):
        partner_without_bot = Partner.objects.create(
            name="No Bot Partner",
            slug="no-bot-partner",
            status=Partner.Status.ACTIVE,
        )
        guest_account = TelegramAccount.objects.create(
            telegram_id=601011,
            username="no_bot_guest",
        )
        guest = GuestProfile.objects.create(
            partner=partner_without_bot,
            telegram_account=guest_account,
        )
        NotificationPreference.objects.create(
            partner=partner_without_bot,
            guest=guest,
            marketing_enabled=True,
        )
        campaign = BroadcastCampaign.objects.create(
            partner=partner_without_bot,
            name="Broken promo",
            message="No bot yet",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )

        processed_count = process_scheduled_broadcast_campaigns()

        campaign.refresh_from_db()
        self.assertEqual(processed_count, 0)
        self.assertEqual(campaign.status, BroadcastCampaign.Status.FAILED)
        self.assertIn("No active polling bot", campaign.last_error)


class BroadcastCampaignAdminFlowTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.partner = Partner.objects.create(
            name="Campaign Partner",
            slug="campaign-partner",
            status=Partner.Status.ACTIVE,
        )
        self.owner = User.objects.create_user(
            username="campaign_owner",
            password="pass12345",
            partner=self.partner,
            role=User.Role.OWNER,
            is_staff=True,
            is_active=True,
        )
        self.bot = BotInstance.objects.create(
            partner=self.partner,
            display_name="Campaign Bot",
            username="campaign_partner_bot",
            mode=BotInstance.Mode.POLLING,
            is_active=True,
            token_encrypted="",
        )
        self.bot.set_token("523456789:ABCDEFGHIJKLMNOPQRST_uv-wxyz")
        self.bot.save(update_fields=["token_encrypted"])
        guest_account = TelegramAccount.objects.create(
            telegram_id=651001,
            username="campaign_guest",
        )
        self.guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=guest_account,
        )
        NotificationPreference.objects.create(
            partner=self.partner,
            guest=self.guest,
            marketing_enabled=True,
        )

    def test_audience_stats_count_eligible_recipients(self):
        stats = get_broadcast_audience_stats(partner_id=self.partner.id)

        self.assertEqual(stats.opted_in_guests, 1)
        self.assertEqual(stats.eligible_recipients, 1)
        self.assertEqual(stats.blocked_recipients, 0)

    def test_form_can_queue_campaign_immediately_for_partner_owner(self):
        form = BroadcastCampaignAdminForm(
            data={
                "name": "Friday promo",
                "message": "Hello guests",
                "scheduled_at": "",
                "send_now": "on",
            },
            request=self._build_request(),
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_admin_save_model_queues_campaign_when_send_now_checked(self):
        model_admin = BroadcastCampaignAdmin(BroadcastCampaign, admin.site)
        request = self._build_request()
        campaign = BroadcastCampaign(partner=self.partner)
        form = BroadcastCampaignAdminForm(
            data={
                "partner": str(self.partner.id),
                "name": "Friday promo",
                "message": "Hello guests",
                "scheduled_at": "",
                "send_now": "on",
            },
            instance=campaign,
            request=request,
        )
        self.assertTrue(form.is_valid(), form.errors)

        with patch("apps.notifications.admin.send_broadcast_campaign") as mocked_send:
            model_admin.save_model(request, campaign, form, change=False)

        mocked_send.assert_called_once_with(campaign)

    def test_delivery_readiness_reports_missing_audience(self):
        NotificationPreference.objects.all().delete()
        self.guest.is_subscribed = False
        self.guest.save(update_fields=["is_subscribed", "updated_at"])
        model_admin = BroadcastCampaignAdmin(BroadcastCampaign, admin.site)
        campaign = BroadcastCampaign.objects.create(
            partner=self.partner,
            name="Promo",
            message="Hello guests",
        )

        readiness = model_admin.delivery_readiness(campaign)

        self.assertIn("Не готово", readiness)

    def _build_request(self):
        request = self.factory.get("/admin/")
        request.user = self.owner
        return request


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
