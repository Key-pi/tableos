from decimal import Decimal

from django.test import TestCase

from apps.analytics.services import (
    build_daily_operations_report,
    build_daily_paid_bills_page,
    build_daily_table_detail,
    build_daily_tables_page,
    build_daily_tails_page,
    build_daily_walk_in_sales_page,
)
from apps.billing.models import Payment
from apps.billing.services import create_bill_from_orders, record_payment
from apps.bonuses.services import register_walk_in_sale
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.services import create_order_from_session
from apps.partners.models import Partner
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import TelegramAccount, User


class DailyOperationsReportTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Report Venue",
            slug="report-venue",
            status=Partner.Status.ACTIVE,
            timezone="Europe/Kiev",
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

    def test_daily_report_aggregates_bills_walk_in_and_methods(self):
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
        register_walk_in_sale(
            partner_id=self.partner.id,
            guest=self.session.guest,
            customer_code_snapshot=self.session.guest.customer_code,
            amount="150.00",
            comment="Bar",
            created_by=self.cashier,
        )

        report = build_daily_operations_report(partner_id=self.partner.id)

        self.assertEqual(report.bill_revenue, Decimal("200.00"))
        self.assertEqual(report.walk_in_revenue, Decimal("150.00"))
        self.assertEqual(report.total_revenue, Decimal("350.00"))
        self.assertEqual(report.paid_bills_count, 1)
        self.assertEqual(report.walk_in_sales_count, 1)
        self.assertEqual(report.payment_method_totals[Payment.Method.CASH], Decimal("200.00"))
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
        register_walk_in_sale(
            partner_id=self.partner.id,
            guest=self.session.guest,
            customer_code_snapshot=self.session.guest.customer_code,
            amount="150.00",
            comment="Bar",
            created_by=self.cashier,
        )

        bills_page = build_daily_paid_bills_page(partner_id=self.partner.id, page=1)
        walkins_page = build_daily_walk_in_sales_page(partner_id=self.partner.id, page=1)
        tables_page = build_daily_tables_page(partner_id=self.partner.id, page=1)
        tails_page = build_daily_tails_page(partner_id=self.partner.id, page=1)

        self.assertEqual(bills_page.section, "bills")
        self.assertEqual(bills_page.total_count, 1)
        self.assertEqual(bills_page.items[0]["kind"], "bill")

        self.assertEqual(walkins_page.section, "walkins")
        self.assertEqual(walkins_page.total_count, 1)
        self.assertEqual(walkins_page.items[0]["kind"], "walkin")

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
