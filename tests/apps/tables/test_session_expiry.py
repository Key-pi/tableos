"""A table session nobody uses closes by itself (PQ-008, owner 2026-11-14).

A guest scanned the QR code and left. A session without orders (cancelled ones
do not count), billing requests or bills closes by itself once nothing has
happened for the venue's window («За столом», 4 hours by default). It becomes
«Истекла» (``timed_out``); the visit closes when nobody else sits in it. A
session with an unpaid order stays — the staff close it. The guest's cart keeps
its items and the bot asks for the way of ordering again."""

import threading
from datetime import timedelta
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.billing.models import BillingRequest
from apps.orders.models import Cart, Order
from apps.orders.services import (
    OrderFlowError,
    add_menu_item_for_telegram_user,
    checkout_active_cart_for_telegram_user,
    transition_order_status,
)
from apps.partners.models import PartnerBotSettings
from apps.tables.models import Table, TableSession, TableVisit
from apps.tables.services import activate_table_session, expire_idle_table_sessions
from tests.webapp.test_guest_api import make_venue

GUEST = 880801
FRIEND = 880802


class ExpiryFixture(TestCase):
    def setUp(self):
        self.partner = make_venue("Expiry")
        self.table = Table.objects.create(partner=self.partner, number=3)

    def sit(self, telegram_id=GUEST):
        return activate_table_session(partner_id=self.partner.id, telegram_id=telegram_id,
                                      payload=self.table.deep_link_payload)

    def latte(self):
        from apps.menu.models import MenuItem
        return MenuItem.objects.get(partner=self.partner, name="Лате")

    def add_latte(self, telegram_id=GUEST):
        return add_menu_item_for_telegram_user(partner_id=self.partner.id,
                                               telegram_id=telegram_id,
                                               item_public_id=self.latte().public_id)

    def order(self, telegram_id=GUEST):
        self.add_latte(telegram_id)
        return checkout_active_cart_for_telegram_user(partner_id=self.partner.id,
                                                      telegram_id=telegram_id)

    def later(self, hours):
        return timezone.now() + timedelta(hours=hours)

    def refresh(self, session):
        session.refresh_from_db()
        return session


class IdleSessionTests(ExpiryFixture):
    def test_a_guest_who_scanned_and_left_is_closed_after_four_quiet_hours(self):
        session = self.sit()
        self.assertEqual(expire_idle_table_sessions(now=self.later(5)), 1)
        session = self.refresh(session)
        self.assertEqual(session.status, TableSession.Status.TIMED_OUT)
        self.assertIsNotNone(session.closed_at)
        self.assertEqual(TableVisit.objects.get(id=session.visit_id).status,
                         TableVisit.Status.CLOSED)

    def test_before_the_window_nothing_closes(self):
        session = self.sit()
        self.assertEqual(expire_idle_table_sessions(now=self.later(3)), 0)
        self.assertEqual(self.refresh(session).status, TableSession.Status.ACTIVE)

    def test_the_window_is_the_venues_own(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            table_session_idle_hours=2)
        other = make_venue("Patient")
        other_table = Table.objects.create(partner=other, number=1)
        mine = self.sit()
        theirs = activate_table_session(partner_id=other.id, telegram_id=GUEST,
                                        payload=other_table.deep_link_payload)
        self.assertEqual(expire_idle_table_sessions(now=self.later(3)), 1)
        self.assertEqual(self.refresh(mine).status, TableSession.Status.TIMED_OUT)
        self.assertEqual(self.refresh(theirs).status, TableSession.Status.ACTIVE)

    def test_a_session_with_an_unpaid_order_stays_for_the_staff(self):
        session = self.sit()
        self.order()
        self.assertEqual(expire_idle_table_sessions(now=self.later(30)), 0)
        self.assertEqual(self.refresh(session).status, TableSession.Status.ACTIVE)

    def test_cancelled_orders_do_not_count(self):
        session = self.sit()
        order = self.order()
        transition_order_status(order=order, to_status=Order.Status.CANCELED)
        self.assertEqual(expire_idle_table_sessions(now=self.later(5)), 1)
        self.assertEqual(self.refresh(session).status, TableSession.Status.TIMED_OUT)

    def test_an_open_bill_request_keeps_the_session(self):
        session = self.sit()
        BillingRequest.objects.create(partner=self.partner, table=self.table,
                                      guest=session.guest, table_session=session)
        self.assertEqual(expire_idle_table_sessions(now=self.later(5)), 0)
        self.assertEqual(self.refresh(session).status, TableSession.Status.ACTIVE)

    def test_the_cart_keeps_its_items_and_counts_as_activity(self):
        session = self.sit()
        cart = self.add_latte()
        Cart.objects.filter(id=cart.id).update(updated_at=self.later(2))
        self.assertEqual(expire_idle_table_sessions(now=self.later(5)), 0,
                         "the cart changed three hours ago")
        self.assertEqual(expire_idle_table_sessions(now=self.later(7)), 1)
        self.assertEqual(self.refresh(session).status, TableSession.Status.TIMED_OUT)
        cart.refresh_from_db()
        self.assertEqual(cart.status, Cart.Status.ACTIVE)
        self.assertEqual(cart.items.count(), 1)

    def test_a_friend_still_at_the_table_keeps_the_visit_open(self):
        mine = self.sit(GUEST)
        friends = self.sit(FRIEND)
        self.assertEqual(mine.visit_id, friends.visit_id)
        self.order(FRIEND)
        self.assertEqual(expire_idle_table_sessions(now=self.later(5)), 1)
        self.assertEqual(self.refresh(mine).status, TableSession.Status.TIMED_OUT)
        self.assertEqual(TableVisit.objects.get(id=mine.visit_id).status,
                         TableVisit.Status.ACTIVE)

    def test_an_order_placed_after_the_list_was_built_keeps_the_session(self):
        """The candidates are picked without locks; under the lock the session
        is checked again — an order that appeared in between keeps it open."""

        from apps.tables import services as table_services

        session = self.sit()
        self.order()
        far = self.later(50)
        self.assertFalse(table_services._expire_idle_session(
            session.id, partner_id=self.partner.id, quiet_since=far, now=far))
        self.assertEqual(self.refresh(session).status, TableSession.Status.ACTIVE)

    def test_checkout_after_the_session_closed_asks_for_the_qr_again(self):
        session = self.sit()
        self.add_latte()
        TableSession.objects.filter(id=session.id).update(
            status=TableSession.Status.TIMED_OUT)
        with self.assertRaises(OrderFlowError):
            with transaction.atomic():
                checkout_active_cart_for_telegram_user(partner_id=self.partner.id,
                                                       telegram_id=GUEST)
        self.assertFalse(Order.objects.filter(table_session=session).exists())


class SettingTests(TestCase):
    def test_four_hours_by_default_and_at_least_one(self):
        partner = make_venue("Setting")
        settings = PartnerBotSettings.objects.get(partner=partner)
        self.assertEqual(settings.table_session_idle_hours, 4)
        settings.table_session_idle_hours = 0
        with self.assertRaises(ValidationError):
            settings.full_clean()

    def test_the_task_runs_on_the_schedule(self):
        from django.conf import settings

        from apps.tables.tasks import EXPIRE_IDLE_TABLE_SESSIONS_TASK
        tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
        self.assertIn(EXPIRE_IDLE_TABLE_SESSIONS_TASK, tasks)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL only")
class ExpiryCheckoutRaceTests(TransactionTestCase):
    """The guest orders at the very moment the session expires: either the
    order lands in an open session or the checkout is refused — never an
    order in a closed session. Both commands lock the guest's cart first."""

    def test_never_an_order_in_a_closed_session(self):
        partner = make_venue("RaceExpiry")
        table = Table.objects.create(partner=partner, number=1)
        session = activate_table_session(partner_id=partner.id, telegram_id=GUEST,
                                         payload=table.deep_link_payload)
        from apps.menu.models import MenuItem
        add_menu_item_for_telegram_user(
            partner_id=partner.id, telegram_id=GUEST,
            item_public_id=MenuItem.objects.get(partner=partner, name="Лате").public_id)
        later = timezone.now() + timedelta(hours=5)
        barrier = threading.Barrier(2)
        results = {}

        def checkout():
            try:
                barrier.wait()
                checkout_active_cart_for_telegram_user(partner_id=partner.id,
                                                       telegram_id=GUEST)
                results["checkout"] = "ordered"
            except OrderFlowError:
                results["checkout"] = "refused"
            finally:
                close_old_connections()

        def expire():
            try:
                barrier.wait()
                results["expired"] = expire_idle_table_sessions(now=later)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=checkout), threading.Thread(target=expire)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        session.refresh_from_db()
        orders = Order.objects.filter(table_session=session).count()
        if session.status == TableSession.Status.TIMED_OUT:
            self.assertEqual(orders, 0)
            self.assertEqual(results, {"checkout": "refused", "expired": 1})
        else:
            self.assertEqual(orders, 1)
            self.assertEqual(results, {"checkout": "ordered", "expired": 0})

    def test_a_checkout_waits_while_the_session_is_being_closed(self):
        """Expiry holds its locks (paused just before it closes the session);
        the guest presses «Оформить». The checkout waits for the cart and is
        then refused — it cannot slip an order into the closing session."""

        from unittest import mock

        from apps.tables import services as table_services

        partner = make_venue("PausedExpiry")
        table = Table.objects.create(partner=partner, number=1)
        session = activate_table_session(partner_id=partner.id, telegram_id=GUEST,
                                         payload=table.deep_link_payload)
        from apps.menu.models import MenuItem
        add_menu_item_for_telegram_user(
            partner_id=partner.id, telegram_id=GUEST,
            item_public_id=MenuItem.objects.get(partner=partner, name="Лате").public_id)
        paused, release = threading.Event(), threading.Event()
        real_activity = table_services._last_session_activity
        results = {}

        def slow_activity(locked_session):
            paused.set()
            release.wait(10)
            return real_activity(locked_session)

        def expire():
            try:
                results["expired"] = expire_idle_table_sessions(
                    now=timezone.now() + timedelta(hours=5))
            finally:
                close_old_connections()

        def checkout():
            try:
                checkout_active_cart_for_telegram_user(partner_id=partner.id,
                                                       telegram_id=GUEST)
                results["checkout"] = "ordered"
            except OrderFlowError:
                results["checkout"] = "refused"
            finally:
                close_old_connections()

        with mock.patch.object(table_services, "_last_session_activity", slow_activity):
            expiring = threading.Thread(target=expire)
            expiring.start()
            self.assertTrue(paused.wait(10))
            ordering = threading.Thread(target=checkout)
            ordering.start()
            ordering.join(1)
            self.assertTrue(ordering.is_alive(), "the checkout must wait for the cart")
            release.set()
            expiring.join(10)
            ordering.join(10)
        session.refresh_from_db()
        self.assertEqual(session.status, TableSession.Status.TIMED_OUT)
        self.assertEqual(results, {"expired": 1, "checkout": "refused"})
        self.assertFalse(Order.objects.filter(table_session=session).exists())
