from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.billing.models import Bill, BillItem, Payment
from apps.billing.services import (
    BillingServiceError,
    create_bill_from_orders,
    pay_bill_with_bonuses,
    record_payment,
)
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.bonuses.services import get_bonus_ledger_balance
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Order
from apps.orders.services import create_order_from_session
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import GuestProfile


def _run_concurrently(callables):
    """Start every callable at the same moment on its own DB connection."""

    barrier = Barrier(len(callables))

    def _worker(fn):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                return fn()
            except BillingServiceError:
                return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(callables)) as executor:
        return list(executor.map(_worker, callables))


@skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency test")
class BillingConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Billing concurrency venue",
            slug="billing-concurrency-venue",
            status=Partner.Status.ACTIVE,
        )
        PartnerBotSettings.objects.create(partner=self.partner, module_billing_enabled=True)
        self.table = Table.objects.create(
            partner=self.partner,
            number=1,
            name="Table 1",
            qr_token="billing-concurrency-01",
        )
        category = MenuCategory.objects.create(partner=self.partner, name="Bar")
        self.menu_item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            name="Lemonade",
            price="150.00",
        )
        self.session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=981001,
            payload=self.table.deep_link_payload,
            username="billing_concurrency_guest",
        )
        self.guest = self.session.guest
        BonusProgram.objects.create(
            partner=self.partner,
            name="Cashback",
            trigger_event=BonusProgram.TriggerEvent.ORDER_COMPLETED,
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00",
            max_redeem_share="30.00",
        )

    def _order(self, quantity=2):
        return create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": quantity,
                }
            ],
        )

    def _seed_balance(self, amount: str):
        BonusTransaction.objects.create(
            partner=self.partner,
            guest=self.guest,
            transaction_type=BonusTransaction.TransactionType.MANUAL,
            amount=amount,
        )
        GuestProfile.objects.filter(id=self.guest.id).update(loyalty_balance=Decimal(amount))

    def test_concurrent_bill_creation_allocates_each_order_item_once(self):
        order = self._order()

        def _create():
            create_bill_from_orders(partner_id=self.partner.id, order_ids=[str(order.id)])
            return "created"

        results = _run_concurrently([_create, _create])

        self.assertEqual(sorted(results), ["created", "rejected"])
        self.assertEqual(Bill.objects.filter(partner=self.partner).count(), 1)
        self.assertEqual(
            BillItem.objects.filter(order_item__order=order).count(),
            order.items.count(),
        )

    def test_concurrent_bonus_payments_never_overspend_the_balance(self):
        # Two personal bills of one guest, 300.00 each; the 30% cap allows 90.00
        # per bill, but the guest only has 100.00 in total.
        self._seed_balance("100.00")
        bills = [
            create_bill_from_orders(
                partner_id=self.partner.id,
                order_ids=[str(self._order().id)],
                kind=Bill.Kind.PERSONAL,
            )
            for _ in range(2)
        ]

        def _pay(bill):
            return lambda: pay_bill_with_bonuses(bill=bill).bonus_spent_amount

        results = _run_concurrently([_pay(bills[0]), _pay(bills[1])])

        self.assertEqual(sorted(results), [Decimal("10.00"), Decimal("90.00")])
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.loyalty_balance, Decimal("0.00"))
        self.assertEqual(
            get_bonus_ledger_balance(partner_id=self.partner.id, guest_id=self.guest.id),
            Decimal("0.00"),
        )

    def test_concurrent_final_payments_settle_once_and_accrue_once(self):
        order = self._order()
        bill = create_bill_from_orders(partner_id=self.partner.id, order_ids=[str(order.id)])

        def _pay():
            record_payment(bill=bill, amount=bill.total_amount, method=Payment.Method.CASH)
            return "paid"

        results = _run_concurrently([_pay, _pay])

        self.assertEqual(sorted(results), ["paid", "rejected"])
        bill.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.PAID)
        self.assertEqual(Payment.objects.filter(bill=bill).count(), 1)
        self.assertIsNotNone(order.paid_at)
        accruals = BonusTransaction.objects.filter(
            partner=self.partner,
            guest=self.guest,
            transaction_type=BonusTransaction.TransactionType.ACCRUAL,
            order=order,
        )
        self.assertEqual(accruals.count(), 1)
        self.assertEqual(accruals.get().amount, Decimal("15.00"))
