from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from apps.analytics.models import PartnerDailyMetric
from apps.analytics.services import (
    build_daily_operations_report,
    build_daily_paid_bills_page,
    build_daily_table_detail,
    build_daily_tables_page,
    build_daily_tails_page,
    refresh_daily_partner_metrics,
    refresh_partner_daily_metric,
)
from apps.billing.counter_sales import sell_at_counter
from apps.billing.models import Payment
from apps.billing.services import create_bill_from_orders, record_payment
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.services import create_order, create_order_from_session
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import TelegramAccount, User


class DailyOperationsReportTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Report Venue",
            slug="report-venue",
            status=Partner.Status.ACTIVE,
            timezone="Europe/Kyiv",
        )
        self.table = Table.objects.create(
            partner=self.partner,
            number=3,
            name="Table 3",
            qr_token="report03",
        )
        category = MenuCategory.objects.create(
            partner=self.partner,
            name="Bar",
            sort_order=10,
        )
        self.menu_item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="REP001",
            name="Tea",
            price="100.00",
            sort_order=10,
        )
        self.session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=710001,
            payload=self.table.deep_link_payload,
            username="report_guest",
            first_name="Report",
        )
        self.order = create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": 2,
                }
            ],
        )
        self.cashier = User.objects.create_user(
            username="cashier_report",
            password="tableos12345",
            partner=self.partner,
            role=User.Role.CASHIER,
            first_name="Cash",
            last_name="ier",
        )
        self.guest_account = TelegramAccount.objects.get(telegram_id=710001)

    def _counter_sale(self):
        """A 150 UAH sale at the counter to the table's guest, by name (PQ-054)."""

        PartnerBotSettings.objects.update_or_create(
            partner=self.partner,
            defaults={"module_quick_sale_enabled": True, "module_menu_enabled": True},
        )
        hookah = MenuItem.objects.create(
            partner=self.partner, category=self.menu_item.category, public_id="REP002",
            name="Tobacco", price="150.00",
        )
        seller = EmployeeProfile.objects.get_or_create(partner=self.partner, user=self.cashier)[0]
        return sell_at_counter(
            partner_id=self.partner.id, seller=seller,
            customer_code=self.session.guest.customer_code, comment="Bar", payment="cash",
            items=[{"menu_item_id": hookah.id, "quantity": 1}],
        )

    def test_daily_report_aggregates_bills_counter_sales_and_methods(self):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )
        record_payment(
            bill=bill,
            amount="200.00",
            method=Payment.Method.CASH,
            created_by=self.cashier,
        )
        self._counter_sale()

        report = build_daily_operations_report(partner_id=self.partner.id)

        # The sale at the counter is a bill too (PQ-054).
        self.assertEqual(report.bill_revenue, Decimal("350.00"))
        self.assertEqual(report.total_revenue, Decimal("350.00"))
        self.assertEqual(report.paid_bills_count, 2)
        self.assertEqual(report.payment_method_totals[Payment.Method.CASH], Decimal("350.00"))
        counter = next(row for row in report.channel_breakdown if row["channel"] == "counter")
        self.assertEqual((counter["orders_count"], counter["revenue"]), (1, Decimal("150.00")))
        self.assertTrue(any(item["label"] == "Cash ier" for item in report.staff_breakdown))

    def test_daily_report_pages_return_operational_journal_entries(self):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )
        record_payment(
            bill=bill,
            amount="200.00",
            method=Payment.Method.CASH,
            created_by=self.cashier,
        )
        self._counter_sale()

        bills_page = build_daily_paid_bills_page(partner_id=self.partner.id, page=1)
        tables_page = build_daily_tables_page(partner_id=self.partner.id, page=1)
        tails_page = build_daily_tails_page(partner_id=self.partner.id, page=1)

        self.assertEqual(bills_page.section, "bills")
        self.assertEqual(bills_page.total_count, 2)  # the table's and the counter's
        self.assertEqual(bills_page.items[0]["kind"], "bill")

        self.assertEqual(tables_page.section, "tables")
        self.assertGreaterEqual(tables_page.total_count, 1)
        self.assertEqual(tables_page.items[0]["kind"], "table")

        self.assertEqual(tails_page.section, "tails")

    def test_daily_table_detail_contains_orders_bills_and_positions(self):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )
        record_payment(
            bill=bill,
            amount="200.00",
            method=Payment.Method.TERMINAL,
            created_by=self.cashier,
        )

        detail = build_daily_table_detail(
            partner_id=self.partner.id,
            table_id=self.table.id,
        )

        self.assertEqual(detail.table_number, self.table.number)
        self.assertGreaterEqual(detail.session_count, 1)
        self.assertEqual(detail.order_count, 1)
        self.assertEqual(detail.paid_bill_count, 1)
        self.assertEqual(detail.orders[0]["public_id"], self.order.public_id)
        self.assertEqual(detail.orders[0]["positions"][0]["name"], self.menu_item.name)

    def test_daily_tails_page_handles_off_table_orders(self):
        off_table_order = create_order(
            partner_id=self.partner.id,
            guest=self.session.guest,
            table=None,
            table_session=None,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": 1,
                }
            ],
            comment="Off-table order",
        )

        tails_page = build_daily_tails_page(partner_id=self.partner.id, page=1)
        order_labels = [item["label"] for item in tails_page.items if item["kind"] == "order"]

        self.assertTrue(
            any(
                off_table_order.public_id in label and "заказ без привязки к столу" in label
                for label in order_labels
            )
        )

    def test_refresh_partner_daily_metric_builds_snapshot(self):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )
        record_payment(
            bill=bill,
            amount="200.00",
            method=Payment.Method.CASH,
            created_by=self.cashier,
        )
        self._counter_sale()

        metric = refresh_partner_daily_metric(partner_id=self.partner.id)

        self.assertEqual(metric.revenue, Decimal("350.00"))
        self.assertEqual(metric.orders_count, 2)
        self.assertEqual(metric.average_check, Decimal("175.00"))
        self.assertGreaterEqual(metric.active_guests, 1)

    def test_refresh_daily_partner_metrics_skips_non_active_partners(self):
        suspended_partner = Partner.objects.create(
            name="Suspended Report Venue",
            slug="suspended-report-venue",
            status=Partner.Status.SUSPENDED,
            timezone="Europe/Kyiv",
        )

        refreshed_count = refresh_daily_partner_metrics()

        self.assertEqual(refreshed_count, 1)
        self.assertTrue(PartnerDailyMetric.objects.filter(partner=self.partner).exists())
        self.assertFalse(PartnerDailyMetric.objects.filter(partner=suspended_partner).exists())

    def test_refresh_daily_partner_metrics_finalises_the_previous_day(self):
        # AA-029: a payment after the last run of a day must reach that day's
        # snapshot once the next day starts.
        kyiv = ZoneInfo("Europe/Kyiv")
        late_evening = datetime(2026, 10, 22, 23, 55, tzinfo=kyiv)
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )
        payment = record_payment(
            bill=bill,
            amount="200.00",
            method=Payment.Method.CASH,
            created_by=self.cashier,
        )
        Payment.objects.filter(id=payment.id).update(paid_at=late_evening)
        stale_metric = PartnerDailyMetric.objects.create(
            partner=self.partner,
            bucket_date=late_evening.date(),
            revenue=Decimal("0.00"),
        )
        PartnerDailyMetric.objects.filter(id=stale_metric.id).update(
            updated_at=datetime(2026, 10, 22, 23, 50, tzinfo=kyiv)
        )

        with patch(
            "apps.analytics.services.timezone.now",
            return_value=datetime(2026, 10, 23, 0, 5, tzinfo=kyiv).astimezone(dt_timezone.utc),
        ):
            refresh_daily_partner_metrics()

        stale_metric.refresh_from_db()
        self.assertEqual(stale_metric.revenue, Decimal("200.00"))

    def test_refresh_partner_daily_metric_uses_requested_bucket_date(self):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )
        payment = record_payment(
            bill=bill,
            amount="200.00",
            method=Payment.Method.CASH,
            created_by=self.cashier,
        )
        payment.paid_at = timezone.now() - timedelta(days=1)
        payment.save(update_fields=["paid_at", "updated_at"])
        bill.refresh_from_db()
        bill.closed_at = payment.paid_at
        bill.save(update_fields=["closed_at", "updated_at"])

        metric = refresh_partner_daily_metric(
            partner_id=self.partner.id,
            bucket_date=payment.paid_at.astimezone(ZoneInfo(self.partner.timezone)).date(),
        )

        self.assertEqual(metric.revenue, Decimal("200.00"))
