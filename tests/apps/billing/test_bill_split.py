"""A bill paid in equal parts (PQ-009, owner 2026-11-16).

One hookah for three: the bill stays one, the staff split it into N equal
parts and take each part by cash or card; the bill is paid when every part
is. The parts share what is left to pay, the last takes the rounding. A part
is paid once — the database keeps it so. Staff who take payments (owner,
manager, cashier) split; nobody re-splits a bill once a part is paid.
"""

import threading
from decimal import Decimal
from unittest import skipUnless

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase

from apps.billing.bill_split import (
    BillSplitError,
    pay_split_part,
    split_bill_equally,
    split_parts_of,
)
from apps.billing.models import Bill, Payment
from apps.billing.services import create_bill_from_orders, issue_bill, record_payment
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.services import create_order_from_session
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import User

CASH, CARD = Payment.Method.CASH, Payment.Method.TERMINAL


def employee(partner, username, role=User.Role.CASHIER, **extra):
    user = User.objects.create_user(username=username, password="x", partner=partner, role=role)
    return EmployeeProfile.objects.create(partner=partner, user=user, **extra)


def venue_with_bill(slug, price="450.00", telegram_id=7720001):
    partner = Partner.objects.create(name=slug, slug=slug, status=Partner.Status.ACTIVE)
    PartnerBotSettings.objects.create(partner=partner, module_billing_enabled=True)
    table = Table.objects.create(partner=partner, number=3, qr_token=f"{slug}-t3"[:32])
    category = MenuCategory.objects.create(partner=partner, name="Кальяни")
    hookah = MenuItem.objects.create(partner=partner, category=category, name="Кальян",
                                     price=price)
    session = activate_table_session(partner_id=partner.id, telegram_id=telegram_id,
                                     payload=table.deep_link_payload)
    order = create_order_from_session(
        partner_id=partner.id, table_session=session,
        items=[{"menu_item_id": hookah.id, "item_name": hookah.name,
                "unit_price": hookah.price, "quantity": 1}],
    )
    bill = issue_bill(create_bill_from_orders(partner_id=partner.id,
                                              order_ids=[str(order.id)]))
    return partner, bill


def amounts(bill):
    bill = Bill.objects.get(id=bill.id)
    return [(part.number, str(part.amount), part.paid) for part in split_parts_of(bill)]


class SplitFixture(TestCase):
    def setUp(self):
        self.partner, self.bill = venue_with_bill("split-venue")
        self.cashier = employee(self.partner, "split_cashier")

    def split(self, parts, by=None):
        return split_bill_equally(partner_id=self.partner.id, employee=by or self.cashier,
                                  bill_id=self.bill.id, parts=parts)

    def pay(self, part, method=CASH, by=None, expected=None):
        return pay_split_part(partner_id=self.partner.id, employee=by or self.cashier,
                              bill_id=self.bill.id, part=part, method=method,
                              expected_amount=expected)


class EqualPartsTests(SplitFixture):
    def test_one_hookah_for_three(self):
        self.split(3)
        self.assertEqual(amounts(self.bill), [(1, "150.00", False), (2, "150.00", False),
                                              (3, "150.00", False)])
        self.pay(1, CASH)
        self.pay(2, CARD)
        self.assertEqual(Bill.objects.get(id=self.bill.id).status, Bill.Status.PARTIALLY_PAID)
        self.pay(3, CASH)
        bill = Bill.objects.get(id=self.bill.id)
        self.assertEqual(bill.status, Bill.Status.PAID)
        self.assertEqual(
            sorted(bill.payments.values_list("split_part", "method", "amount")),
            [(1, CASH, Decimal("150.00")), (2, CARD, Decimal("150.00")),
             (3, CASH, Decimal("150.00"))],
        )

    def test_the_last_part_takes_the_rounding(self):
        _partner, self.bill = venue_with_bill("split-hundred", price="100.00",
                                              telegram_id=7720002)
        self.partner = _partner
        self.cashier = employee(self.partner, "split_cashier_100")
        self.split(3)
        self.assertEqual([amount for _n, amount, _p in amounts(self.bill)],
                         ["33.33", "33.33", "33.34"])
        self.pay(1)
        self.assertEqual(amounts(self.bill), [(1, "33.33", True), (2, "33.33", False),
                                              (3, "33.34", False)])
        self.pay(3)
        self.pay(2)
        self.assertEqual(Bill.objects.get(id=self.bill.id).paid_amount, Decimal("100.00"))

    def test_the_parts_share_what_is_left_to_pay(self):
        record_payment(bill=self.bill, amount="150.00", method=CASH)
        self.split(2)
        self.assertEqual([amount for _n, amount, _p in amounts(self.bill)],
                         ["150.00", "150.00"])

    def test_a_part_is_paid_once(self):
        self.split(3)
        self.pay(2)
        with self.assertRaisesMessage(BillSplitError, "уже оплачена"):
            self.pay(2, CARD)
        self.assertEqual(Payment.objects.filter(bill=self.bill).count(), 1)

    def test_the_amount_on_the_screen_must_still_be_right(self):
        self.split(3)
        with self.assertRaisesMessage(BillSplitError, "обновите"):
            self.pay(1, expected=Decimal("225.00"))
        self.assertFalse(Payment.objects.exists())
        self.pay(1, expected=Decimal("150.00"))

    def test_split_again_or_undo_only_before_a_part_is_paid(self):
        self.split(3)
        self.split(2)
        self.assertEqual(len(amounts(self.bill)), 2)
        self.split(0)
        self.assertEqual(amounts(self.bill), [])
        self.split(3)
        self.pay(1)
        for parts in (2, 0):
            with self.assertRaisesMessage(BillSplitError, "уже оплачена"):
                self.split(parts)
        self.assertEqual(Bill.objects.get(id=self.bill.id).split_parts, 3)

    def test_a_full_payment_still_closes_a_split_bill(self):
        self.split(3)
        self.pay(1)
        record_payment(bill=self.bill, amount="300.00", method=CARD)
        self.assertEqual(Bill.objects.get(id=self.bill.id).status, Bill.Status.PAID)
        self.assertEqual(amounts(self.bill), [(1, "150.00", True), (2, "0.00", False),
                                              (3, "0.00", False)])
        with self.assertRaises(BillSplitError):
            self.pay(2)

    def test_limits(self):
        for parts in (1, 21, -2):
            with self.assertRaises(BillSplitError):
                self.split(parts)
        with self.assertRaisesMessage(BillSplitError, "не делили"):
            self.pay(1)
        self.split(3)
        for part in (0, 4):
            with self.assertRaises(BillSplitError):
                self.pay(part)
        with self.assertRaises(BillSplitError):
            self.pay(1, method=Payment.Method.BONUSES)

    def test_a_closed_bill_is_not_split(self):
        Bill.objects.filter(id=self.bill.id).update(status=Bill.Status.CANCELED)
        with self.assertRaises(BillSplitError):
            self.split(2)

    def test_the_database_keeps_a_part_paid_once(self):
        Payment.objects.create(partner=self.partner, bill=self.bill, status=Payment.Status.PAID,
                               method=CASH, amount="150.00", split_part=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(partner=self.partner, bill=self.bill,
                                   status=Payment.Status.PAID, method=CARD, amount="150.00",
                                   split_part=1)


class WhoSplitsTests(SplitFixture):
    def test_staff_who_take_payments(self):
        manager = employee(self.partner, "split_manager", User.Role.MANAGER)
        self.split(2, by=manager)
        self.pay(1, by=manager)

    def test_not_a_waiter_nor_another_venue_nor_a_dismissed_cashier(self):
        waiter = employee(self.partner, "split_waiter", User.Role.WAITER)
        other, _other_bill = venue_with_bill("split-other", telegram_id=7720003)
        stranger = employee(other, "split_stranger")
        dismissed = employee(self.partner, "split_gone", is_active=False)
        for who in (waiter, stranger, dismissed):
            with self.assertRaises(BillSplitError):
                self.split(2, by=who)
        self.split(2)
        for who in (waiter, stranger, dismissed):
            with self.assertRaises(BillSplitError):
                self.pay(1, by=who)
        self.assertFalse(Payment.objects.exists())

    def test_the_bill_of_another_venue_is_not_found(self):
        other, other_bill = venue_with_bill("split-foreign", telegram_id=7720004)
        with self.assertRaises(BillSplitError):
            split_bill_equally(partner_id=self.partner.id, employee=self.cashier,
                               bill_id=other_bill.id, parts=2)
        self.assertEqual(Bill.objects.get(id=other_bill.id).split_parts, 0)


@skipUnless(connection.vendor == "postgresql", "нужны построчные блокировки")
class SamePartAtOnceTests(TransactionTestCase):
    """Two cashiers tap the same part at once: the bill's lock makes them take
    turns, the second finds the part paid."""

    def test_one_payment(self):
        partner, bill = venue_with_bill("split-race", telegram_id=7720005)
        first, second = employee(partner, "split_race_1"), employee(partner, "split_race_2")
        split_bill_equally(partner_id=partner.id, employee=first, bill_id=bill.id, parts=3)
        start = threading.Barrier(2)
        errors = []

        def tap(who):
            try:
                start.wait(5)
                pay_split_part(partner_id=partner.id, employee=who, bill_id=bill.id, part=2,
                               method=CASH)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=tap, args=(who,)) for who in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([type(error).__name__ for error in errors], ["BillSplitError"])
        self.assertEqual(Payment.objects.filter(bill=bill, split_part=2).count(), 1)
