from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.orders.models import Order
from apps.orders.services import OrderFlowError, transition_order_status
from apps.partners.models import Partner
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import TelegramAccount


@skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency test")
class OrderTransitionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Order concurrency venue",
            slug="order-concurrency-venue",
            status=Partner.Status.ACTIVE,
        )
        table = Table.objects.create(
            partner=self.partner,
            number=1,
            name="Table 1",
            qr_token="order-concurrency-01",
        )
        account = TelegramAccount.objects.create(
            telegram_id=980001,
            username="order_concurrency_guest",
        )
        session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=account.telegram_id,
            payload=table.deep_link_payload,
            username=account.username,
        )
        self.order = Order.objects.create(
            partner=self.partner,
            guest=session.guest,
            table=table,
            table_session=session,
        )

    @staticmethod
    def _transition(order_id, partner_id, barrier):
        close_old_connections()
        try:
            stale_order = Order.objects.get(id=order_id, partner_id=partner_id)
            barrier.wait(timeout=10)
            try:
                transition_order_status(
                    order=stale_order,
                    to_status=Order.Status.ACCEPTED,
                )
                return "accepted"
            except OrderFlowError:
                return "rejected"
        finally:
            close_old_connections()

    def test_concurrent_duplicate_transition_has_one_effect(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: self._transition(
                        self.order.id,
                        self.partner.id,
                        barrier,
                    ),
                    range(2),
                )
            )

        self.order.refresh_from_db()
        self.assertEqual(results.count("accepted"), 1)
        self.assertEqual(results.count("rejected"), 1)
        self.assertEqual(self.order.status, Order.Status.ACCEPTED)
        self.assertEqual(
            self.order.status_history.filter(to_status=Order.Status.ACCEPTED).count(),
            1,
        )
