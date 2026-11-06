"""B4 delivery contract: broadcasts at most once, notifications at least once.

PQ-014 (broadcasts) and PQ-015 (operational notifications), AA-008, AA-030.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.notifications import tasks
from apps.notifications.models import (
    BroadcastCampaign,
    BroadcastDelivery,
    NotificationPreference,
    StaffNotification,
)
from apps.notifications.services import (
    RetryableDeliveryError,
    deliver_staff_notification_now,
    recover_stalled_broadcast_campaigns,
    send_broadcast_campaign,
    send_broadcast_campaign_now,
    send_guest_order_status_update_now,
)
from apps.orders.services import create_order_from_session
from apps.partners.models import BotInstance, Partner
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import GuestProfile, TelegramAccount, User

SENDER = "apps.notifications.services._send_telegram_messages"


class RecordingSender:
    """Fake Telegram sender that records every chat id it was asked to send to."""

    # A broken guarantee can turn into an endless resend loop; fail instead.
    MAX_MESSAGES = 50

    def __init__(self, outcome=None, delay=0.0):
        self.sent: list[int] = []
        self.outcome = outcome or (lambda chat_id: (chat_id, True, "", False, False))
        self.delay = delay
        self.lock = threading.Lock()

    async def __call__(self, _token, messages, *, on_result=None):
        results = []
        for chat_id, _text in messages:
            if self.delay:
                time.sleep(self.delay)
            with self.lock:
                self.sent.append(chat_id)
                if len(self.sent) > self.MAX_MESSAGES:
                    raise AssertionError("Sender called repeatedly: messages are being resent.")
            result = self.outcome(chat_id)
            results.append(result)
            if on_result is not None:
                await on_result(result)
        return results


class DeliveryFixtureMixin:
    def _make_partner(self, slug="delivery-venue"):
        self.partner = Partner.objects.create(
            name="Delivery Venue",
            slug=slug,
            status=Partner.Status.ACTIVE,
        )
        bot = BotInstance.objects.create(
            partner=self.partner,
            display_name="Delivery Bot",
            username=f"{slug.replace('-', '_')}_bot",
            mode=BotInstance.Mode.POLLING,
            is_active=True,
            token_encrypted="",
        )
        bot.set_token("223456789:ABCDEFGHIJKLMNOPQRST_uv-wxyz")
        bot.save(update_fields=["token_encrypted"])

    def _make_guests(self, count, start=880000):
        guests = []
        for index in range(count):
            account = TelegramAccount.objects.create(telegram_id=start + index)
            guest = GuestProfile.objects.create(partner=self.partner, telegram_account=account)
            NotificationPreference.objects.create(
                partner=self.partner,
                guest=guest,
                marketing_enabled=True,
            )
            guests.append(guest)
        return guests

    def _campaign(self, name="Promo"):
        return BroadcastCampaign.objects.create(
            partner=self.partner,
            name=name,
            message="Hello",
        )

    def _statuses(self, campaign):
        return sorted(campaign.deliveries.values_list("status", flat=True))


class BroadcastAtMostOnceTests(DeliveryFixtureMixin, TestCase):
    def setUp(self):
        self._make_partner()

    def _run_all(self, campaign, sender):
        with patch(SENDER, new=sender):
            with self.captureOnCommitCallbacks(execute=True):
                send_broadcast_campaign(campaign)
        campaign.refresh_from_db()
        return campaign

    def test_audience_is_recorded_and_each_guest_gets_one_message(self):
        guests = self._make_guests(3)
        sender = RecordingSender()
        campaign = self._run_all(self._campaign(), sender)

        self.assertEqual(sorted(sender.sent), [g.telegram_account.telegram_id for g in guests])
        self.assertEqual(campaign.status, BroadcastCampaign.Status.SENT)
        self.assertEqual(campaign.delivered_count, 3)
        self.assertEqual(self._statuses(campaign), ["sent"] * 3)

    def test_duplicate_or_retried_task_sends_nothing_again(self):
        self._make_guests(2)
        sender = RecordingSender()
        campaign = self._run_all(self._campaign(), sender)

        with patch(SENDER, new=sender):
            with self.captureOnCommitCallbacks(execute=True):
                send_broadcast_campaign_now(campaign)
                tasks.send_broadcast_campaign_task.apply(args=[str(campaign.id), ""])
                send_broadcast_campaign(campaign)

        self.assertEqual(len(sender.sent), 2)

    def test_requeue_after_failure_reaches_only_unattempted_guests(self):
        # Batches go in guest id order: the first guest is attempted first.
        first, second = sorted(self._make_guests(2), key=lambda guest: guest.id)
        campaign = self._campaign()
        # The first guest's message definitely failed; the second was never tried.
        outcome = lambda chat_id: (chat_id, False, "Bad Request: chat not found", False, False)  # noqa: E731
        with patch("apps.notifications.services.BROADCAST_BATCH_SIZE", 1):
            with patch(SENDER, new=RecordingSender(outcome)):
                with self.captureOnCommitCallbacks(execute=False):
                    send_broadcast_campaign_now(campaign)
        self.partner.status = Partner.Status.SUSPENDED
        self.partner.save(update_fields=["status", "updated_at"])
        with self.captureOnCommitCallbacks(execute=True):
            send_broadcast_campaign_now(campaign)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, BroadcastCampaign.Status.FAILED)

        self.partner.status = Partner.Status.ACTIVE
        self.partner.save(update_fields=["status", "updated_at"])
        late = self._make_guests(1, start=880100)[0]
        sender = RecordingSender()
        campaign = self._run_all(campaign, sender)

        self.assertEqual(
            sorted(sender.sent),
            [second.telegram_account.telegram_id, late.telegram_account.telegram_id],
        )
        self.assertEqual(campaign.status, BroadcastCampaign.Status.SENT)
        self.assertEqual((campaign.delivered_count, campaign.failed_count), (2, 1))
        self.assertNotIn(first.telegram_account.telegram_id, sender.sent)

    def test_guest_who_opted_out_after_start_is_skipped(self):
        first, second = sorted(self._make_guests(2), key=lambda guest: guest.id)
        campaign = self._campaign()
        sender = RecordingSender()
        with patch("apps.notifications.services.BROADCAST_BATCH_SIZE", 1):
            with patch(SENDER, new=sender):
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    send_broadcast_campaign_now(campaign)
                NotificationPreference.objects.filter(guest=second).update(marketing_enabled=False)
                with self.captureOnCommitCallbacks(execute=True):
                    for callback in callbacks:
                        callback()

        campaign.refresh_from_db()
        self.assertEqual(sender.sent, [first.telegram_account.telegram_id])
        self.assertEqual(campaign.deliveries.get(guest=second).status, "skipped")
        self.assertEqual(campaign.status, BroadcastCampaign.Status.SENT)

    def test_ambiguous_telegram_failure_is_unknown_and_never_resent(self):
        self._make_guests(1)
        outcome = lambda chat_id: (chat_id, False, "Request timeout", False, True)  # noqa: E731
        sender = RecordingSender(outcome)
        campaign = self._run_all(self._campaign(), sender)
        with patch(SENDER, new=sender):
            with self.captureOnCommitCallbacks(execute=True):
                send_broadcast_campaign(campaign)

        campaign.refresh_from_db()
        self.assertEqual(len(sender.sent), 1)
        self.assertEqual(self._statuses(campaign), ["unknown"])
        self.assertEqual(campaign.unknown_count, 1)
        self.assertEqual(campaign.status, BroadcastCampaign.Status.FAILED)

    def test_worker_crash_after_send_is_never_resent_and_recovered(self):
        guests = self._make_guests(2)
        campaign = self._campaign()
        sent: list[int] = []

        async def crashing_sender(_token, messages, *, on_result=None):
            sent.extend(chat_id for chat_id, _text in messages)
            raise RuntimeError("worker killed after Telegram accepted the messages")

        with patch(SENDER, new=crashing_sender):
            with self.assertRaises(RuntimeError):
                send_broadcast_campaign_now(campaign)
        self.assertEqual(self._statuses(campaign), ["sending", "sending"])

        sender = RecordingSender()
        with patch(SENDER, new=sender):
            with self.captureOnCommitCallbacks(execute=True):
                send_broadcast_campaign_now(campaign)
                recover_stalled_broadcast_campaigns()
        self.assertEqual(sender.sent, [])
        self.assertEqual(self._statuses(campaign), ["sending", "sending"])

        with self.captureOnCommitCallbacks(execute=True):
            touched = recover_stalled_broadcast_campaigns(now=timezone.now() + timedelta(hours=1))

        campaign.refresh_from_db()
        self.assertEqual(touched, 1)
        self.assertEqual(sorted(sent), [g.telegram_account.telegram_id for g in guests])
        self.assertEqual(self._statuses(campaign), ["unknown", "unknown"])
        self.assertEqual(campaign.unknown_count, 2)
        self.assertEqual(campaign.status, BroadcastCampaign.Status.FAILED)

    def test_recovery_requeues_a_campaign_whose_next_task_was_lost(self):
        first, second = sorted(self._make_guests(2), key=lambda guest: guest.id)
        campaign = self._campaign()
        sender = RecordingSender()
        with patch("apps.notifications.services.BROADCAST_BATCH_SIZE", 1):
            with patch(SENDER, new=sender):
                with self.captureOnCommitCallbacks(execute=False):
                    send_broadcast_campaign_now(campaign)  # next-batch task is lost
                with self.captureOnCommitCallbacks(execute=True):
                    self.assertEqual(recover_stalled_broadcast_campaigns(), 0)
                    touched = recover_stalled_broadcast_campaigns(
                        now=timezone.now() + timedelta(hours=1)
                    )

        campaign.refresh_from_db()
        self.assertEqual(touched, 1)
        self.assertEqual(
            sender.sent,
            [first.telegram_account.telegram_id, second.telegram_account.telegram_id],
        )
        self.assertEqual(campaign.status, BroadcastCampaign.Status.SENT)

    def test_legacy_in_flight_campaign_resumes_after_old_cursor(self):
        first, second, third = sorted(self._make_guests(3), key=lambda guest: guest.id)
        campaign = self._campaign()
        BroadcastCampaign.objects.filter(id=campaign.id).update(
            status=BroadcastCampaign.Status.SENDING,
            delivered_count=1,
        )
        sender = RecordingSender()
        with patch(SENDER, new=sender):
            with self.captureOnCommitCallbacks(execute=True):
                send_broadcast_campaign_now(campaign, after_guest_id=str(first.id))

        campaign.refresh_from_db()
        self.assertEqual(
            sender.sent,
            [second.telegram_account.telegram_id, third.telegram_account.telegram_id],
        )
        self.assertFalse(campaign.deliveries.filter(guest=first).exists())
        self.assertEqual(campaign.status, BroadcastCampaign.Status.SENT)

    def test_legacy_in_flight_campaign_without_cursor_is_not_resent(self):
        self._make_guests(2)
        campaign = self._campaign()
        BroadcastCampaign.objects.filter(id=campaign.id).update(
            status=BroadcastCampaign.Status.SENDING,
            delivered_count=1,
        )
        sender = RecordingSender()
        with patch(SENDER, new=sender):
            send_broadcast_campaign_now(campaign)

        campaign.refresh_from_db()
        self.assertEqual(sender.sent, [])
        self.assertEqual(campaign.status, BroadcastCampaign.Status.FAILED)
        self.assertIn("interrupted", campaign.last_error)


class DeliveryTaskOptionsTests(TestCase):
    def test_delivery_tasks_are_acknowledged_late(self):
        for task in (
            tasks.deliver_staff_notification_task,
            tasks.send_guest_order_status_update_task,
            tasks.send_broadcast_campaign_task,
        ):
            self.assertTrue(task.acks_late, task.name)
            self.assertTrue(task.reject_on_worker_lost, task.name)


class StaffNotificationAtLeastOnceTests(DeliveryFixtureMixin, TestCase):
    def setUp(self):
        self._make_partner(slug="staff-delivery-venue")
        user = User.objects.create_user(
            username="delivery_staff",
            password="pass12345",
            partner=self.partner,
            role=User.Role.MANAGER,
        )
        account = TelegramAccount.objects.create(telegram_id=881001)
        self.employee = EmployeeProfile.objects.create(
            partner=self.partner,
            user=user,
            telegram_account=account,
            bot_notifications_enabled=True,
        )
        self.notification = StaffNotification.objects.create(
            partner=self.partner,
            employee=self.employee,
            category=StaffNotification.Category.ORDER_CREATED,
            title="New order",
            message="Order arrived",
        )

    def test_sent_notification_is_not_sent_again(self):
        sender = RecordingSender()
        with patch(SENDER, new=sender):
            deliver_staff_notification_now(self.notification)
            deliver_staff_notification_now(self.notification)
        self.assertEqual(sender.sent, [881001])

    def test_transient_failure_is_retried_until_sent(self):
        failing = RecordingSender(lambda chat_id: (chat_id, False, "Bad Gateway", False, True))
        with patch(SENDER, new=failing):
            with self.assertRaises(RetryableDeliveryError):
                deliver_staff_notification_now(self.notification)
        self.notification.refresh_from_db()
        self.assertEqual(self.notification.delivery_status, StaffNotification.DeliveryStatus.FAILED)

        sender = RecordingSender()
        with patch(SENDER, new=sender):
            deliver_staff_notification_now(self.notification)
        self.notification.refresh_from_db()
        self.assertEqual(sender.sent, [881001])
        self.assertEqual(self.notification.delivery_status, StaffNotification.DeliveryStatus.SENT)

    def test_definite_failure_is_recorded_without_retry(self):
        failing = RecordingSender(lambda chat_id: (chat_id, False, "chat not found", False, False))
        with patch(SENDER, new=failing):
            deliver_staff_notification_now(self.notification)
        self.notification.refresh_from_db()
        self.assertEqual(self.notification.delivery_status, StaffNotification.DeliveryStatus.FAILED)
        self.assertEqual(self.notification.delivery_error, "chat not found")

    def test_task_retries_a_transient_failure(self):
        attempts = []

        def outcome(chat_id):
            attempts.append(chat_id)
            if len(attempts) == 1:
                return (chat_id, False, "Bad Gateway", False, True)
            return (chat_id, True, "", False, False)

        with patch(SENDER, new=RecordingSender(outcome)):
            tasks.deliver_staff_notification_task.apply(
                args=[str(self.notification.id)],
                throw=False,
            )
        self.notification.refresh_from_db()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.notification.delivery_status, StaffNotification.DeliveryStatus.SENT)

    def test_deactivated_employee_gets_no_queued_notification(self):
        for field, value in (("is_active", False), ("bot_notifications_enabled", False)):
            with self.subTest(field=field):
                EmployeeProfile.objects.filter(id=self.employee.id).update(**{field: value})
                sender = RecordingSender()
                with patch(SENDER, new=sender):
                    deliver_staff_notification_now(self.notification)
                self.notification.refresh_from_db()
                self.assertEqual(sender.sent, [])
                self.assertEqual(
                    self.notification.delivery_status,
                    StaffNotification.DeliveryStatus.FAILED,
                )
                EmployeeProfile.objects.filter(id=self.employee.id).update(**{field: True})
                StaffNotification.objects.filter(id=self.notification.id).update(
                    delivery_status=StaffNotification.DeliveryStatus.PENDING
                )

    def test_deactivated_user_gets_no_queued_notification(self):
        User.objects.filter(id=self.employee.user_id).update(is_active=False)
        sender = RecordingSender()
        with patch(SENDER, new=sender):
            deliver_staff_notification_now(self.notification)
        self.assertEqual(sender.sent, [])


class GuestOrderUpdateAtLeastOnceTests(DeliveryFixtureMixin, TestCase):
    def setUp(self):
        self._make_partner(slug="guest-update-venue")
        table = Table.objects.create(
            partner=self.partner,
            number=1,
            name="Table 1",
            qr_token="guest_update_tbl",
        )
        category = MenuCategory.objects.create(partner=self.partner, name="Bar", sort_order=1)
        item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="GUPD0001",
            name="Tea",
            price="50.00",
            sort_order=1,
        )
        session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=882001,
            payload=table.deep_link_payload,
            username="update_guest",
        )
        self.order = create_order_from_session(
            partner_id=self.partner.id,
            table_session=session,
            items=[
                {
                    "menu_item_id": item.id,
                    "item_name": item.name,
                    "unit_price": item.price,
                    "quantity": 1,
                }
            ],
        )

    def _send(self):
        return send_guest_order_status_update_now(
            order_id=str(self.order.id),
            from_status="new",
            to_status="accepted",
        )

    def test_transient_failure_raises_for_retry(self):
        sender = RecordingSender(lambda chat_id: (chat_id, False, "timeout", False, True))
        with patch(SENDER, new=sender):
            with self.assertRaises(RetryableDeliveryError):
                self._send()

    def test_definite_failure_is_not_retried(self):
        sender = RecordingSender(lambda chat_id: (chat_id, False, "blocked", True, False))
        with patch(SENDER, new=sender):
            self.assertFalse(self._send())
        self.order.guest.telegram_account.refresh_from_db()
        self.assertTrue(self.order.guest.telegram_account.is_blocked)


class NoTelegramCallInsideTransactionTests(DeliveryFixtureMixin, TransactionTestCase):
    """AA-008: Telegram I/O never runs while a database transaction is open."""

    def setUp(self):
        self._make_partner(slug="no-tx-venue")

    def test_broadcast_sends_with_no_transaction_open(self):
        self._make_guests(2)
        campaign = self._campaign()
        seen_atomic = []

        async def checking_sender(_token, messages, *, on_result=None):
            seen_atomic.append(connection.in_atomic_block)
            results = []
            for chat_id, _text in messages:
                result = (chat_id, True, "", False, False)
                results.append(result)
                if on_result is not None:
                    await on_result(result)
            return results

        with patch(SENDER, new=checking_sender):
            send_broadcast_campaign(campaign)

        campaign.refresh_from_db()
        self.assertEqual(seen_atomic, [False])
        self.assertEqual(campaign.status, BroadcastCampaign.Status.SENT)
        self.assertEqual(campaign.delivered_count, 2)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency test")
class BroadcastConcurrencyTests(DeliveryFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self._make_partner(slug="broadcast-race-venue")

    def test_parallel_workers_never_send_a_guest_twice(self):
        guests = self._make_guests(6)
        campaign = self._campaign()
        sender = RecordingSender(delay=0.05)
        barrier = Barrier(3)

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                send_broadcast_campaign_now(campaign)
            finally:
                close_old_connections()

        with patch("apps.notifications.services.BROADCAST_BATCH_SIZE", 2):
            with patch(SENDER, new=sender):
                with ThreadPoolExecutor(max_workers=3) as executor:
                    futures = [executor.submit(worker) for _ in range(3)]
                    for future in futures:
                        future.result(timeout=60)

        campaign.refresh_from_db()
        expected = sorted(guest.telegram_account.telegram_id for guest in guests)
        self.assertEqual(sorted(sender.sent), expected)
        self.assertEqual(
            BroadcastDelivery.objects.filter(campaign=campaign, status="sent").count(),
            6,
        )
        self.assertEqual(campaign.status, BroadcastCampaign.Status.SENT)
        self.assertEqual(campaign.delivered_count, 6)
