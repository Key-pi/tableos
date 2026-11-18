"""People and purchases in the day's numbers (PQ-009, step 2; owner 2026-11-18).

Splitting a bill must not change what a company spent: a table visit is one
purchase however many bills it was paid by, so the average check is per
purchase. «Гостей за столами» counts the people the staff said sat there
(never fewer than the guests listed), with the bot or without; the check
per guest is the tables' money over them.
"""

from datetime import date
from decimal import Decimal

from apps.analytics.models import PartnerDailyMetric
from apps.analytics.services import build_daily_operations_report, refresh_partner_daily_metric
from apps.billing.models import Bill, Payment
from apps.billing.services import record_payment
from apps.billing.table_bills import create_personal_bills_for_table, create_shared_bill_for_table
from apps.tables.models import Table
from apps.tables.services import set_visit_guests_count
from tests.apps.orders.test_staff_table_orders import TableOrderFixture


class GuestMetricsTests(TableOrderFixture):
    def pay_all(self, bills):
        for bill in bills:
            bill = Bill.objects.get(id=bill.id)
            record_payment(bill=bill, amount=bill.total_amount - bill.paid_amount,
                           method=Payment.Method.CASH)

    def setUp(self):
        super().setUp()
        # A company of four at table 5: two of them ordered, each pays alone.
        first = self.order()  # 2 × latte = 160
        visit_id = first.table_session.visit_id
        self.order(visit=visit_id, new_guest=True,
                   items=[{"menu_item_id": self.hookah.id, "quantity": 1}])
        set_visit_guests_count(partner_id=self.partner.id, actor=self.waiter,
                               visit_id=visit_id, count=4)
        bills = create_personal_bills_for_table(partner_id=self.partner.id,
                                                table_id=self.table.id, visit_id=visit_id)
        self.assertEqual(len(bills), 2)
        self.pay_all(bills)
        # One guest at table 6, one bill.
        alone = self.order(table=self.other_table)
        self.pay_all([create_shared_bill_for_table(
            partner_id=self.partner.id, table_id=self.other_table.id,
            visit_id=alone.table_session.visit_id)])
        self.revenue = sum(
            (bill.total_amount for bill in Bill.objects.filter(status=Bill.Status.PAID)),
            Decimal("0.00"))

    def test_a_visit_is_one_purchase_however_it_was_split(self):
        report = build_daily_operations_report(partner_id=self.partner.id)
        self.assertEqual((report.paid_bills_count, report.purchases_count), (3, 2))
        self.assertEqual(report.average_check, (self.revenue / 2).quantize(Decimal("0.01")))

    def test_the_people_at_the_tables(self):
        report = build_daily_operations_report(partner_id=self.partner.id)
        self.assertEqual(report.table_guests, 5)  # four said at 5, one at 6
        self.assertEqual(report.average_per_guest,
                         (self.revenue / 5).quantize(Decimal("0.01")))

    def test_the_daily_metric_keeps_them(self):
        refresh_partner_daily_metric(partner_id=self.partner.id)
        metric = PartnerDailyMetric.objects.get(partner=self.partner)
        self.assertEqual((metric.sales_count, metric.table_guests), (2, 5))
        self.assertEqual(metric.average_check, (self.revenue / 2).quantize(Decimal("0.01")))
        self.assertEqual(metric.average_per_guest, (self.revenue / 5).quantize(Decimal("0.01")))

    def test_nothing_at_the_tables(self):
        Table.objects.create(partner=self.partner, number=8)
        report = build_daily_operations_report(partner_id=self.partner.id,
                                               bucket_date=date(2020, 1, 1))
        self.assertEqual((report.purchases_count, report.table_guests), (0, 0))
        self.assertEqual((report.average_check, report.average_per_guest),
                         (Decimal("0.00"), Decimal("0.00")))

    def test_the_bot_and_the_dashboard_say_so(self):
        from bot.handlers.staff import _render_day_report

        report = build_daily_operations_report(partner_id=self.partner.id)
        text = _render_day_report(report)
        for line in ("Покупок: 2", "Гостей за столами: 5", "Оплаченных счетов: 3"):
            self.assertIn(line, text)
        from apps.users.admin_dashboard import _today

        class Request:
            user = self.waiter.user

        from unittest.mock import patch

        with patch("apps.users.admin_dashboard._can", return_value=True):
            today = _today(Request(), self.partner.id)
        self.assertEqual((today["sales"], today["table_guests"]), (2, 5))
