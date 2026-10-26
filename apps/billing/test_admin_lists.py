"""Admin lists of bills and orders cost the same number of queries for any page size.

The bill list made about 500 queries for a page of 100 bills (several per
row: partner, table, guest, and the remaining amount re-read the bill, its
lines and payments); the order list about 250. 20 owners at once waited 2.1 s
for 95% of pages (load test 2026-10-25, AA-046).
"""

from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.billing import tests as billing_tests
from apps.billing.models import Payment
from apps.billing.services import (
    create_bill_from_orders,
    get_bill_remaining_amount,
    record_payment,
)
from apps.orders.services import create_order_from_session
from apps.users.models import User


class AdminListQueryCountTests(TestCase):
    setUp = billing_tests.BillingServiceTests.setUp

    def _billed_order(self, *, pay: Decimal | None = None):
        order = create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": 1,
                }
            ],
        )
        bill = create_bill_from_orders(partner_id=self.partner.id, order_ids=[order.id])
        if pay is not None:
            record_payment(
                bill=bill, amount=pay, method=Payment.Method.CASH, created_by=self.manager.user
            )
        return bill

    def _queries(self, url) -> int:
        admin = User.objects.filter(is_superuser=True).first() or User.objects.create_superuser(
            username="lists_admin", password="pass12345"
        )
        self.client.force_login(admin)
        self.client.get(url)  # warm caches (content types, permissions)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_bill_list_does_not_query_per_row(self):
        self._billed_order()
        self._billed_order(pay=Decimal("50.00"))
        few = self._queries("/admin/billing/bill/")
        for _unused in range(5):
            self._billed_order(pay=Decimal("20.00"))
        self.assertEqual(self._queries("/admin/billing/bill/"), few)

    def test_order_list_does_not_query_per_row(self):
        self._billed_order()
        few = self._queries("/admin/orders/order/")
        for _unused in range(5):
            self._billed_order()
        self.assertEqual(self._queries("/admin/orders/order/"), few)

    def test_bill_list_shows_the_same_remaining_amount(self):
        bills = [self._billed_order(), self._billed_order(pay=Decimal("100.00"))]
        admin = User.objects.create_superuser(username="amount_admin", password="pass12345")
        self.client.force_login(admin)
        response = self.client.get("/admin/billing/bill/")
        content = response.content.decode()
        for bill in bills:
            expected = get_bill_remaining_amount(bill)
            self.assertIn(f">{expected}<", content.replace(",", "."))
