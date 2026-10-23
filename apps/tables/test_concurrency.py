from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.partners.models import Partner
from apps.tables.models import Table, TableSession, TableVisit
from apps.tables.services import (
    VISIT_CHOICE_ASK,
    VisitChoiceRequired,
    activate_table_session,
)
from apps.users.models import GuestProfile, TelegramAccount


@skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency test")
class TableSessionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Session concurrency venue",
            slug="session-concurrency-venue",
            status=Partner.Status.ACTIVE,
        )
        self.tables = [
            Table.objects.create(
                partner=self.partner,
                number=number,
                name=f"Table {number}",
                qr_token=f"session-concurrency-{number}",
            )
            for number in (1, 2)
        ]
        account = TelegramAccount.objects.create(
            telegram_id=980002,
            username="session_concurrency_guest",
        )
        self.guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=account,
        )
        self.telegram_id = account.telegram_id

    def _activate(self, table, barrier):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return activate_table_session(
                partner_id=self.partner.id,
                telegram_id=self.telegram_id,
                payload=table.deep_link_payload,
                username="session_concurrency_guest",
            )
        finally:
            close_old_connections()

    def test_concurrent_two_table_scans_leave_one_active_session(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            sessions = list(
                executor.map(
                    lambda table: self._activate(table, barrier),
                    self.tables,
                )
            )

        active_sessions = TableSession.objects.filter(
            partner=self.partner,
            guest=self.guest,
            status=TableSession.Status.ACTIVE,
        )
        self.assertEqual(active_sessions.count(), 1)
        self.assertEqual(
            TableSession.objects.filter(
                id__in=[session.id for session in sessions],
                status=TableSession.Status.CLOSED,
            ).count(),
            1,
        )


@skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency test")
class TableVisitConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Visit concurrency venue",
            slug="visit-concurrency-venue",
            status=Partner.Status.ACTIVE,
        )
        self.table = Table.objects.create(
            partner=self.partner,
            number=1,
            name="Table 1",
            qr_token="visit-concurrency-1",
        )

    def _scan(self, telegram_id, barrier):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                activate_table_session(
                    partner_id=self.partner.id,
                    telegram_id=telegram_id,
                    payload=self.table.deep_link_payload,
                    visit_choice=VISIT_CHOICE_ASK,
                )
                return "started"
            except VisitChoiceRequired:
                return "asked"
        finally:
            close_old_connections()

    def test_two_guests_scanning_an_empty_table_get_one_visit_and_one_question(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(lambda tg_id: self._scan(tg_id, barrier), (980101, 980102))
            )

        self.assertEqual(sorted(results), ["asked", "started"])
        self.assertEqual(TableVisit.objects.filter(table=self.table).count(), 1)
        self.assertEqual(TableSession.objects.filter(table=self.table).count(), 1)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency test")
class VisitCloseVersusJoinConcurrencyTests(TransactionTestCase):
    """Payment closing a visit races a guest joining it (lock order, ADR-0007)."""

    reset_sequences = True

    def setUp(self):
        from apps.billing.services import create_bill_from_orders
        from apps.menu.models import MenuCategory, MenuItem
        from apps.orders.services import confirm_order_received_by_guest, create_order_from_session

        self.partner = Partner.objects.create(
            name="Race venue", slug="race-venue", status=Partner.Status.ACTIVE
        )
        self.table = Table.objects.create(partner=self.partner, number=1, qr_token="race-1")
        category = MenuCategory.objects.create(partner=self.partner, name="Bar")
        item = MenuItem.objects.create(
            partner=self.partner, category=category, name="Tea", price="50.00"
        )
        self.session = activate_table_session(
            partner_id=self.partner.id, telegram_id=980201, payload=self.table.deep_link_payload
        )
        order = create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[
                {"menu_item_id": item.id, "item_name": "Tea", "unit_price": "50.00", "quantity": 1}
            ],
        )
        confirm_order_received_by_guest(
            partner_id=self.partner.id, telegram_id=980201, order_public_id=order.public_id
        )
        self.bill = create_bill_from_orders(partner_id=self.partner.id, order_ids=[str(order.id)])

    def _pay(self, barrier):
        from apps.billing.models import Payment
        from apps.billing.services import record_payment

        close_old_connections()
        try:
            barrier.wait(timeout=10)
            record_payment(bill=self.bill, amount="50.00", method=Payment.Method.CASH)
            return "paid"
        finally:
            close_old_connections()

    def _join(self, barrier):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            session = activate_table_session(
                partner_id=self.partner.id,
                telegram_id=980202,
                payload=self.table.deep_link_payload,
            )
            return str(session.visit_id)
        finally:
            close_old_connections()

    def test_no_deadlock_and_no_session_in_a_closed_visit(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            paid = executor.submit(self._pay, barrier)
            joined = executor.submit(self._join, barrier)
            self.assertEqual(paid.result(timeout=30), "paid")
            joined.result(timeout=30)

        self.assertFalse(
            TableSession.objects.filter(
                status=TableSession.Status.ACTIVE,
                visit__status=TableVisit.Status.CLOSED,
            ).exists()
        )
