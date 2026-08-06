from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.orders.models import Order
from apps.partners.models import Partner
from apps.tables.models import Table, TableSession
from apps.tables.services import activate_table_session, can_close_table_session
from apps.users.models import TelegramAccount


class TableSessionSettlementTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Tables Test Venue",
            slug="tables-test-venue",
            status=Partner.Status.ACTIVE,
        )
        self.table = Table.objects.create(
            partner=self.partner,
            number=4,
            name="Table 4",
            qr_token="tables04",
        )
        self.telegram_account = TelegramAccount.objects.create(
            telegram_id=440001,
            username="table_guest",
        )
        self.session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            payload=self.table.deep_link_payload,
            username=self.telegram_account.username,
            first_name="Table",
        )
        self.order = Order.objects.create(
            partner=self.partner,
            guest=self.session.guest,
            table=self.table,
            table_session=self.session,
            total_amount="320.00",
        )

    def test_can_close_session_when_order_is_paid_and_received(self):
        self.order.status = Order.Status.COMPLETED
        self.order.paid_at = self.order.updated_at
        self.order.received_at = self.order.updated_at
        self.order.save(update_fields=["status", "paid_at", "received_at", "updated_at"])

        can_close, reason = can_close_table_session(self.session)

        self.assertTrue(can_close)
        self.assertEqual(reason, "")

    def test_cannot_close_session_with_paid_but_unreceived_order(self):
        self.order.status = Order.Status.READY
        self.order.paid_at = self.order.updated_at
        self.order.save(update_fields=["status", "paid_at", "updated_at"])

        can_close, reason = can_close_table_session(self.session)

        self.assertFalse(can_close)
        self.assertIn("получение", reason)

    def test_cannot_close_session_with_received_but_unpaid_order(self):
        self.order.status = Order.Status.COMPLETED
        self.order.received_at = self.order.updated_at
        self.order.save(update_fields=["status", "received_at", "updated_at"])

        can_close, reason = can_close_table_session(self.session)

        self.assertFalse(can_close)
        self.assertIn("без оплаты", reason)

    def test_only_one_active_session_can_exist_for_guest_in_partner(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TableSession.objects.create(
                    partner=self.partner,
                    guest=self.session.guest,
                    table=self.table,
                )

        closed_session = TableSession.objects.create(
            partner=self.partner,
            guest=self.session.guest,
            table=self.table,
            status=TableSession.Status.CLOSED,
        )
        self.assertEqual(closed_session.status, TableSession.Status.CLOSED)
