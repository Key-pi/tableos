"""The settlement editor of a table (PQ-009, step 3; owner 2026-11-18).

«Рассчитать» lays the company's lines out by guests: each line goes, whole,
to a guest's own bill or to «Общее», which the guests who share it pay in
equal parts. A guest pays their bill and their part of «Общее» with one tap.
The cashback is theirs who pay, not theirs who ordered; an order is paid when
every bill with its lines is. Lines already paid for stay where they are.
"""

import threading
from decimal import Decimal
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.billing.bill_split import BillSplitError, split_bill_equally
from apps.billing.models import Bill, BillAdjustment, BillItem, Payment, Refund
from apps.billing.services import refund_bill_lines
from apps.billing.visit_settlement import (
    SHARED,
    SettlementError,
    lay_out_visit,
    pay_for_guest,
    visit_settlement,
)
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.employees.models import EmployeeProfile
from apps.orders.models import Order
from apps.tables.models import Table, TableVisit
from apps.tables.services import visit_unsettled_reason
from apps.users.models import User
from tests.apps.orders.test_staff_table_orders import TableOrderFixture, venue
from tests.webapp.test_staff_api import staff

CASH, CARD = Payment.Method.CASH, Payment.Method.TERMINAL


def cashier(partner, username="kava_cashier", telegram_id=7702100):
    return staff(partner, username, User.Role.CASHIER, telegram_id)


class SettlementFixture(TableOrderFixture):
    """Олена (with the bot) — a latte; Гость 1 — two lattes; Гость 2 — a hookah."""

    def setUp(self):
        super().setUp()
        self.cashier = cashier(self.partner)
        olena_session = self.scan(7702001)
        self.visit = olena_session.visit
        self.olena = olena_session.guest
        self.olena_order = self.order(visit=self.visit.id, session=olena_session.id,
                                      items=[{"menu_item_id": self.latte.id, "quantity": 1}])
        self.first_order = self.order(visit=self.visit.id)  # 2 × 80
        self.second_order = self.order(visit=self.visit.id, new_guest=True,
                                       items=[{"menu_item_id": self.hookah.id, "quantity": 1}])
        self.first, self.second = self.first_order.guest, self.second_order.guest
        self.line = {
            "olena": self.olena_order.items.get().id,
            "first": self.first_order.items.get().id,
            "hookah": self.second_order.items.get().id,
        }

    def settlement(self):
        return visit_settlement(partner_id=self.partner.id, visit_id=self.visit.id)

    def guest_row(self, guest):
        return next(row for row in self.settlement().guests if row.guest_id == guest.id)

    def lay_out(self, places, sharers=(), by=None):
        return lay_out_visit(
            partner_id=self.partner.id, employee=by or self.cashier, visit_id=self.visit.id,
            places={str(self.line[name]): (where if where == SHARED else str(where.id))
                    for name, where in places.items()},
            sharers=[str(guest.id) for guest in sharers],
        )

    def by_guests(self, hookah=SHARED, sharers=None):
        return self.lay_out(
            {"olena": self.olena, "first": self.first, "hookah": hookah},
            sharers=(self.olena, self.first, self.second) if sharers is None else sharers,
        )

    def pay(self, guest, method=CASH, expected=None, by=None):
        return pay_for_guest(partner_id=self.partner.id, employee=by or self.cashier,
                             visit_id=self.visit.id, guest_id=guest.id, method=method,
                             expected_amount=expected)


class EditorTests(SettlementFixture):
    def test_the_editor_starts_from_who_ordered(self):
        settlement = self.settlement()
        self.assertEqual([row.label for row in settlement.guests],
                         [self.olena.staff_label(), "Гость 1", "Гость 2"])
        places = {line.order_item_id: line.place for line in settlement.lines}
        self.assertEqual(places, {self.line["olena"]: self.olena.id,
                                  self.line["first"]: self.first.id,
                                  self.line["hookah"]: self.second.id})
        self.assertFalse(any(line.fixed for line in settlement.lines))
        self.assertFalse(settlement.laid_out)
        self.assertEqual([(row.unbilled, row.to_pay) for row in settlement.guests],
                         [(Decimal("80.00"), Decimal("0.00")), (Decimal("160.00"), Decimal("0.00")),
                          (Decimal("400.00"), Decimal("0.00"))])

    def test_own_bills_and_the_shared_one(self):
        bills = self.by_guests()
        self.assertEqual(len(bills), 3)
        shared = next(bill for bill in bills if bill.split_shares.exists())
        self.assertEqual((shared.total_amount, shared.split_parts), (Decimal("400.00"), 3))
        self.assertEqual(list(shared.split_shares.values_list("number", "guest_id")),
                         [(1, self.olena.id), (2, self.first.id), (3, self.second.id)])
        olena_bill = next(bill for bill in bills if bill.primary_guest_id == self.olena.id)
        self.assertEqual((olena_bill.kind, olena_bill.total_amount),
                         (Bill.Kind.PERSONAL, Decimal("80.00")))
        self.assertEqual([row.to_pay for row in self.settlement().guests],
                         [Decimal("213.33"), Decimal("293.33"), Decimal("133.34")])
        self.assertEqual(self.settlement().sharers, [self.olena.id, self.first.id, self.second.id])

    def test_a_guest_pays_with_one_tap(self):
        self.by_guests()
        payments = self.pay(self.olena, expected=Decimal("213.33"))
        self.assertEqual(sorted((payment.amount, payment.split_part) for payment in payments),
                         [(Decimal("80.00"), None), (Decimal("133.33"), 1)])
        self.assertEqual(self.guest_row(self.olena).to_pay, Decimal("0.00"))
        self.pay(self.first, CARD)
        self.pay(self.second)
        self.assertEqual(set(Bill.objects.filter(visit=self.visit).exclude(
            status=Bill.Status.CANCELED).values_list("status", flat=True)), {Bill.Status.PAID})
        self.assertFalse(Order.objects.filter(paid_at__isnull=True).exists())
        self.assertIsNone(visit_unsettled_reason(TableVisit.objects.get(id=self.visit.id)))

    def test_one_pays_for_the_other(self):
        self.lay_out({"olena": self.olena, "first": self.olena, "hookah": self.second})
        self.assertEqual([row.to_pay for row in self.settlement().guests],
                         [Decimal("240.00"), Decimal("0.00"), Decimal("400.00")])
        with self.assertRaisesMessage(SettlementError, "нечего"):
            self.pay(self.first)

    def test_an_order_is_paid_when_all_its_lines_are(self):
        both = self.order(visit=self.visit.id, session=self.settlement().guests[1].session_id,
                          items=[{"menu_item_id": self.latte.id, "quantity": 1},
                                 {"menu_item_id": self.hookah.id, "quantity": 1}])
        latte, hookah = both.items.order_by("unit_price")
        self.line.update(both_latte=latte.id, both_hookah=hookah.id)
        self.lay_out({"olena": self.olena, "first": self.first, "hookah": self.second,
                      "both_latte": self.first, "both_hookah": self.olena})
        self.pay(self.first)
        both.refresh_from_db()
        self.assertIsNone(both.paid_at)
        self.pay(self.olena)
        both.refresh_from_db()
        self.assertIsNotNone(both.paid_at)

    def test_laid_out_again_before_any_money(self):
        first_bills = self.by_guests()
        self.lay_out({"olena": self.olena, "first": self.olena, "hookah": self.olena})
        for bill in first_bills:
            bill.refresh_from_db()
            self.assertEqual(bill.status, Bill.Status.CANCELED)
            self.assertEqual(bill.adjustments.get().kind, BillAdjustment.Kind.RELAYOUT)
        live = Bill.objects.filter(visit=self.visit).exclude(status=Bill.Status.CANCELED)
        self.assertEqual(list(live.values_list("primary_guest_id", "total_amount")),
                         [(self.olena.id, Decimal("640.00"))])
        self.assertEqual(BillItem.objects.filter(is_active=True).count(), 3)
        # The editor opens as it was laid out.
        self.assertEqual({line.place for line in self.settlement().lines}, {self.olena.id})

    def test_lines_paid_for_stay_where_they_are(self):
        self.by_guests()
        self.pay(self.olena)
        settlement = self.settlement()
        fixed = {line.order_item_id for line in settlement.lines if line.fixed}
        # Her bill is paid, «Общее» in part.
        self.assertEqual(fixed, {self.line["olena"], self.line["hookah"]})
        with self.assertRaisesMessage(SettlementError, "оплачен"):
            self.lay_out({"olena": self.first, "first": self.first})
        self.lay_out({"first": self.second})
        self.assertEqual(self.guest_row(self.second).to_pay, Decimal("293.34"))

    def test_every_line_has_one_place(self):
        with self.assertRaisesMessage(SettlementError, "все позиции"):
            self.lay_out({"olena": self.olena})
        stray = self.order(table=self.other_table)
        self.line["stray"] = stray.items.get().id
        with self.assertRaises(SettlementError):
            self.lay_out({"olena": self.olena, "first": self.first, "hookah": self.second,
                          "stray": self.olena})
        with self.assertRaises(SettlementError):
            self.lay_out({"olena": self.olena, "first": stray.guest, "hookah": self.second})
        with self.assertRaisesMessage(SettlementError, "Общее"):
            self.by_guests(sharers=())
        with self.assertRaises(SettlementError):
            self.by_guests(sharers=(self.olena, stray.guest))
        self.assertFalse(Bill.objects.exists())

    def test_only_who_takes_payments(self):
        foreign = cashier(venue("Other"), "other_cashier", 7702101)
        for who in (self.waiter, foreign):
            with self.assertRaises(SettlementError):
                self.lay_out(
                    {"olena": self.olena, "first": self.first, "hookah": self.second}, by=who)
        self.by_guests()
        with self.assertRaises(SettlementError):
            self.pay(self.olena, by=self.waiter)
        self.assertFalse(Payment.objects.exists())

    def test_the_amount_on_the_screen_must_still_be_right(self):
        self.by_guests()
        with self.assertRaisesMessage(SettlementError, "обновите"):
            self.pay(self.olena, expected=Decimal("80.00"))
        self.assertFalse(Payment.objects.exists())

    def test_a_line_already_in_a_bill_is_not_billed_twice(self):
        from apps.billing.services import BillingServiceError, create_bill_from_lines

        self.by_guests()
        with self.assertRaisesMessage(BillingServiceError, "другой счёт"):
            create_bill_from_lines(partner_id=self.partner.id,
                                   order_item_ids=[self.line["first"]])

    def test_a_guests_request_for_the_bill_follows_their_lines(self):
        from apps.billing.models import BillingRequest
        from apps.billing.table_bills import create_billing_request_for_telegram_user

        result = create_billing_request_for_telegram_user(
            partner_id=self.partner.id, telegram_id=7702001,
            request_type=BillingRequest.RequestType.PERSONAL,
        )
        self.assertEqual(result.bill.primary_guest_id, self.olena.id)
        bills = self.lay_out({"olena": self.olena, "first": self.first, "hookah": self.second})
        result.request.refresh_from_db()
        self.assertEqual(result.request.bill, bills[0])
        self.assertEqual(result.request.bill.primary_guest_id, self.olena.id)
        # Гость 1 pays for her: she has her answer.
        self.lay_out({"olena": self.first, "first": self.first, "hookah": self.second})
        result.request.refresh_from_db()
        self.assertEqual((result.request.bill, result.request.status),
                         (None, BillingRequest.Status.PROCESSED))

    def test_the_shares_of_the_shared_bill_are_the_editors(self):
        bills = self.by_guests()
        shared = next(bill for bill in bills if bill.split_shares.exists())
        with self.assertRaisesMessage(BillSplitError, "Рассчитать"):
            split_bill_equally(partner_id=self.partner.id, employee=self.cashier,
                               bill_id=shared.id, parts=2)


class CashbackTests(SettlementFixture):
    def setUp(self):
        super().setUp()
        BonusProgram.objects.create(
            partner=self.partner, name="Кешбэк", percent="10.00",
            program_type=BonusProgram.ProgramType.CASHBACK, max_redeem_share="20.00",
        )

    def accrued(self, guest):
        return sum(BonusTransaction.objects.filter(
            guest=guest, transaction_type=BonusTransaction.TransactionType.ACCRUAL,
            source_type="order_paid",
        ).values_list("amount", flat=True), Decimal("0.00"))

    def test_the_cashback_is_theirs_who_pay(self):
        # Олена pays for Гость 1's lattes and shares the hookah with Гость 2.
        self.lay_out({"olena": self.olena, "first": self.olena, "hookah": SHARED},
                     sharers=(self.olena, self.second))
        self.pay(self.olena)
        self.assertEqual(self.accrued(self.olena), Decimal("24.00"))  # 10% of 80 + 160
        self.pay(self.second)  # «Общее» is paid: her half of the hookah too
        self.assertEqual(self.accrued(self.olena), Decimal("44.00"))
        self.assertEqual(self.accrued(self.first), Decimal("0.00"))

    def test_a_refund_takes_it_back_from_who_got_it(self):
        bills = self.lay_out({"olena": self.olena, "first": self.olena, "hookah": self.second})
        self.pay(self.olena)
        self.assertEqual(self.accrued(self.olena), Decimal("24.00"))
        olena_bill = next(bill for bill in bills if bill.primary_guest_id == self.olena.id)
        owner = User.objects.create_user(username="kava_owner", password="x",
                                          partner=self.partner, role=User.Role.OWNER)
        EmployeeProfile.objects.get_or_create(partner=self.partner, user=owner)
        first_line = BillItem.objects.get(bill=olena_bill, order_item_id=self.line["first"])
        refund = refund_bill_lines(bill=olena_bill, bill_item_ids=[first_line.id], actor=owner,
                                   reason_code=Refund.ReasonCode.INPUT_ERROR,
                                   method=Refund.Method.CASH)
        self.assertEqual(refund.cashback_revoked, Decimal("16.00"))
        self.olena.refresh_from_db()
        self.assertEqual(self.olena.loyalty_balance, Decimal("8.00"))


@skipUnless(connection.vendor == "postgresql", "нужны построчные блокировки")
class TwoGuestsPayAtOnceTests(TransactionTestCase):
    """Two cashiers take two guests' money at once; both touch «Общее» — the
    bills' locks in id order make them take turns, nobody deadlocks."""

    def test_both_paid(self):
        from apps.menu.models import MenuItem
        from apps.orders.services import create_staff_table_order

        partner = venue("KavaPay")
        table = Table.objects.create(partner=partner, number=4)
        latte = MenuItem.objects.get(partner=partner, name="Лате")
        waiter = staff(partner, "kava_pay_waiter", User.Role.WAITER, 7702201)
        cashiers = [cashier(partner, f"kava_pay_cashier_{n}", 7702210 + n) for n in (1, 2)]
        first = create_staff_table_order(partner_id=partner.id, actor=waiter, table_id=table.id,
                                         items=[{"menu_item_id": latte.id, "quantity": 1}])
        visit_id = first.table_session.visit_id
        second = create_staff_table_order(partner_id=partner.id, actor=waiter, table_id=table.id,
                                          visit_id=visit_id, new_guest=True,
                                          items=[{"menu_item_id": latte.id, "quantity": 2}])
        guests = [first.guest, second.guest]
        lay_out_visit(
            partner_id=partner.id, employee=cashiers[0], visit_id=visit_id,
            places={str(first.items.get().id): str(guests[0].id),
                    str(second.items.get().id): SHARED},
            sharers=[str(guest.id) for guest in guests],
        )
        start = threading.Barrier(2)
        errors = []

        def pay(who, guest):
            try:
                start.wait(5)
                pay_for_guest(partner_id=partner.id, employee=who, visit_id=visit_id,
                              guest_id=guest.id, method=CASH)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=pay, args=pair)
                   for pair in zip(cashiers, guests, strict=True)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(set(Bill.objects.filter(visit_id=visit_id).values_list(
            "status", flat=True)), {Bill.Status.PAID})
        self.assertEqual(Payment.objects.filter(split_part__isnull=False).count(), 2)


@skipUnless(connection.vendor == "postgresql", "нужны построчные блокировки")
class OneGuestPaidTwiceAtOnceTests(TransactionTestCase):
    """Two cashiers tap one guest's «Наличные» at once: the visit's bills are
    locked before the amount is counted, so the second finds nothing to pay."""

    def test_paid_once(self):
        from apps.menu.models import MenuItem
        from apps.orders.services import create_staff_table_order

        partner = venue("KavaTwice")
        table = Table.objects.create(partner=partner, number=4)
        latte = MenuItem.objects.get(partner=partner, name="Лате")
        waiter = staff(partner, "kava_twice_waiter", User.Role.WAITER, 7702301)
        cashiers = [cashier(partner, f"kava_twice_cashier_{n}", 7702310 + n) for n in (1, 2)]
        first = create_staff_table_order(partner_id=partner.id, actor=waiter, table_id=table.id,
                                         items=[{"menu_item_id": latte.id, "quantity": 1}])
        visit_id = first.table_session.visit_id
        second = create_staff_table_order(partner_id=partner.id, actor=waiter, table_id=table.id,
                                          visit_id=visit_id, new_guest=True,
                                          items=[{"menu_item_id": latte.id, "quantity": 2}])
        guests = [first.guest, second.guest]
        lay_out_visit(
            partner_id=partner.id, employee=cashiers[0], visit_id=visit_id,
            places={str(first.items.get().id): str(guests[0].id),
                    str(second.items.get().id): SHARED},
            sharers=[str(guest.id) for guest in guests],
        )
        start = threading.Barrier(2)
        errors = []

        def pay(who):
            try:
                start.wait(5)
                pay_for_guest(partner_id=partner.id, employee=who, visit_id=visit_id,
                              guest_id=guests[0].id, method=CASH)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=pay, args=(who,)) for who in cashiers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([type(error).__name__ for error in errors], ["SettlementError"])
        self.assertEqual(Payment.objects.count(), 2)  # her bill and her part, once


class SharesInAdminTests(SettlementFixture):
    def test_the_shared_bill_shows_whose_part_is_which(self):
        from django.urls import reverse

        bills = self.by_guests()
        owner = User.objects.create_superuser(username="kava_root", password="x", email="")
        self.client.force_login(owner)
        shared = next(bill for bill in bills if bill.split_shares.exists())
        own = next(bill for bill in bills if not bill.split_shares.exists())

        def models(bill):
            page = self.client.get(reverse("admin:billing_bill_change", args=[bill.pk]))
            return {formset.opts.model.__name__
                    for formset in page.context["inline_admin_formsets"]}

        self.assertIn("BillSplitShare", models(shared))
        self.assertNotIn("BillSplitShare", models(own))
