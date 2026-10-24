"""Two staff members enter a ТТН for the same parcel at once (PQ-034)."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.billing.remote_orders import RemoteOrderError, mark_order_shipped
from apps.billing.test_shipping import ShippingFixture
from apps.orders.models import Order
from apps.orders.services import OrderFlowError, transition_order_status
from apps.partners.models import PartnerBotSettings
from apps.users.models import User


@skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency test")
class MarkShippedConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    _destination = ShippingFixture._destination
    _place = ShippingFixture._place

    def setUp(self):
        ShippingFixture.setUp(self)
        PartnerBotSettings.objects.filter(id=self.settings.id).update(shipping_pay_on_receipt=True)
        self._destination()
        order = self._place(payment=Order.PaymentTiming.ON_RECEIPT)
        order = transition_order_status(order=order, to_status=Order.Status.ACCEPTED)
        self.order = transition_order_status(order=order, to_status=Order.Status.READY)

    @staticmethod
    def _ship(order_id, actor_id, number, barrier):
        close_old_connections()
        try:
            stale_order = Order.objects.get(id=order_id)
            actor = User.objects.get(id=actor_id)
            barrier.wait(timeout=10)
            try:
                mark_order_shipped(order=stale_order, actor=actor, tracking_number=number)
                return number
            except (RemoteOrderError, OrderFlowError):
                return "rejected"
        finally:
            close_old_connections()

    def test_concurrent_numbers_ship_the_parcel_once(self):
        barrier = Barrier(2)
        numbers = ["20450000000001", "20450000000002"]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda number: self._ship(self.order.id, self.cashier.id, number, barrier),
                    numbers,
                )
            )

        self.order.refresh_from_db()
        self.assertEqual(results.count("rejected"), 1)
        winner = next(result for result in results if result != "rejected")
        self.assertEqual(self.order.tracking_number, winner)
        self.assertEqual(self.order.status, Order.Status.DELIVERING)
        self.assertEqual(
            self.order.status_history.filter(to_status=Order.Status.DELIVERING).count(), 1
        )
