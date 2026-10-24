"""Auto-cancel and a staff confirmation of the same transfer at once (PQ-036)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.billing.models import Payment
from apps.billing.remote_orders import (
    RemoteOrderError,
    confirm_transfer,
    expire_unconfirmed_transfer,
    save_guest_phone,
)
from apps.billing.test_remote_orders import NOON, TG, PickupFixtureMixin
from apps.orders.models import Order
from apps.users.models import User


@skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency test")
class ExpireVersusConfirmTests(PickupFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        save_guest_phone(partner_id=self.partner.id, telegram_id=TG, phone="+380501112233")
        self._cart()
        self.order = self._place()
        Order.objects.filter(id=self.order.id).update(created_at=NOON - timedelta(hours=2))

    @staticmethod
    def _run(kind, order_id, cashier_id, barrier):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            if kind == "expire":
                canceled = expire_unconfirmed_transfer(order_id=order_id, now=NOON)
                return "canceled" if canceled else "kept"
            order = Order.objects.get(id=order_id)
            try:
                confirm_transfer(order=order, actor=User.objects.get(id=cashier_id))
                return "confirmed"
            except RemoteOrderError:
                return "refused"
        finally:
            close_old_connections()

    def test_exactly_one_of_them_wins(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda kind: self._run(kind, self.order.id, self.cashier.id, barrier),
                    ["expire", "confirm"],
                )
            )
        self.order.refresh_from_db()
        if results == ["canceled", "refused"]:
            self.assertEqual(self.order.status, Order.Status.CANCELED)
            self.assertFalse(Payment.objects.filter(status=Payment.Status.PAID).exists())
        else:
            self.assertEqual(results, ["kept", "confirmed"])
            self.assertEqual(self.order.status, Order.Status.ACCEPTED)
            self.assertIsNotNone(self.order.paid_at)
