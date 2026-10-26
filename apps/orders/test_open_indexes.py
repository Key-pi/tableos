"""Open orders and open bills are found without walking the partner's history.

The staff screens list open orders and open bills by partner. With only the
``partner_id`` index PostgreSQL read every order and bill of the partner
(1000-1700 pages per call after a year, 12-17 ms, growing every month; load
test 2026-10-25). Partial indexes over the open rows keep it to the open ones.
The test checks that PostgreSQL can use them for the real querysets: the
index predicate must follow from each query's filter.
"""

from unittest import skipUnless

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.billing.models import Bill
from apps.billing.repositories import BillingOperationsRepository, BillRepository
from apps.orders.models import Order
from apps.orders.repositories import OrderRepository
from apps.orders.selectors import get_open_orders_for_partner
from apps.partners.models import Partner
from apps.users.models import GuestProfile, TelegramAccount


def _plan(queryset) -> str:
    sql, params = queryset.query.sql_with_params()
    with connection.cursor() as cursor:
        # Only whether the index *can* serve the query matters here, not the
        # planner's choice on a tiny test table.
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("SET LOCAL enable_bitmapscan = off")
        cursor.execute(f"EXPLAIN {sql}", params)
        return "\n".join(row[0] for row in cursor.fetchall())


@skipUnless(connection.vendor == "postgresql", "partial indexes and EXPLAIN are PostgreSQL")
class OpenRowIndexTests(TestCase):
    def setUp(self):
        # A partner with history: many closed orders and bills, a few open
        # ones, and fresh statistics, so the planner compares the indexes as
        # it would on a real database.
        self.partner = Partner.objects.create(name="Index Venue", slug="index-venue")
        account = TelegramAccount.objects.create(telegram_id=7710001)
        guest = GuestProfile.objects.create(partner=self.partner, telegram_account=account)
        now = timezone.now()
        Order.objects.bulk_create(
            Order(
                partner=self.partner,
                guest=guest,
                public_id=f"{n:08x}",
                status=Order.Status.COMPLETED if n >= 3 else Order.Status.NEW,
                paid_at=now if n >= 3 else None,
            )
            for n in range(600)
        )
        Bill.objects.bulk_create(
            Bill(
                partner=self.partner,
                public_id=f"{n:08x}",
                status=Bill.Status.PAID if n >= 2 else Bill.Status.ISSUED,
            )
            for n in range(600)
        )
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE orders_order")
            cursor.execute("ANALYZE billing_bill")

    def test_open_orders_use_the_open_order_index(self):
        for queryset in (
            get_open_orders_for_partner(self.partner.id),
            OrderRepository.open_for_partner(self.partner.id),
            BillingOperationsRepository.unbilled_open_orders_for_partner(self.partner.id),
        ):
            with self.subTest(sql=str(queryset.query)[:80]):
                self.assertIn("order_open_partner_idx", _plan(queryset))

    def test_open_bills_use_the_open_bill_index(self):
        plan = _plan(BillRepository.open_for_partner(self.partner.id))
        self.assertIn("bill_open_partner_idx", plan)
