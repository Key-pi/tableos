"""Two staff members refund the same line at once (PQ-037)."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.billing.models import Bill, BillItem, Refund
from apps.billing.services import BillingServiceError, refund_bill_lines
from apps.billing.test_refunds import RefundSetupMixin
from apps.users.models import User


@skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency test")
class DoubleRefundTests(RefundSetupMixin, TransactionTestCase):
    reset_sequences = True

    @staticmethod
    def _run(bill_id, item_id, owner_id, barrier):
        close_old_connections()
        try:
            bill = Bill.objects.get(id=bill_id)
            owner = User.objects.get(id=owner_id)
            barrier.wait(timeout=10)
            try:
                with patch("apps.notifications.services.send_guest_refund_notice"):
                    refund_bill_lines(
                        bill=bill,
                        bill_item_ids=[item_id],
                        actor=owner,
                        reason_code=Refund.ReasonCode.OUT_OF_STOCK,
                        method=Refund.Method.CASH,
                    )
                return "refunded"
            except BillingServiceError:
                return "refused"
        finally:
            close_old_connections()

    def test_the_line_is_refunded_once(self):
        barrier = Barrier(2)
        juice = BillItem.objects.get(bill=self.bill, item_name="Сок")
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _i: self._run(self.bill.id, juice.id, self.owner.id, barrier),
                    range(2),
                )
            )
        self.assertEqual(sorted(results), ["refunded", "refused"])
        self.assertEqual(Refund.objects.count(), 1)
        self.bill.refresh_from_db()
        self.assertEqual(str(self.bill.refunded_amount), "80.00")
