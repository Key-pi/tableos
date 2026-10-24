"""Refunds of paid bills: full and partial, bonuses and cashback (PQ-037)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from apps.analytics.services import build_daily_operations_report
from apps.billing.models import Bill, BillItem, Refund, RefundLine
from apps.billing.remote_orders import confirm_transfer, place_remote_order, save_guest_phone
from apps.billing.services import BillingServiceError, refund_bill_lines
from apps.billing.test_remote_orders import NOON, TG, PickupFixtureMixin
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuItem
from apps.orders.models import Order
from apps.orders.services import add_items_to_cart_for_telegram_user, transition_order_status
from apps.partners.models import Partner
from apps.users.models import User

REASON = Refund.ReasonCode.OUT_OF_STOCK
CASH = Refund.Method.CASH


class RefundSetupMixin(PickupFixtureMixin):
    def setUp(self):
        super().setUp()
        save_guest_phone(partner_id=self.partner.id, telegram_id=TG, phone="+380501112233")
        MenuItem.objects.create(
            partner=self.partner,
            category=self.item.category,
            public_id="PICK0002",
            name="Сок",
            price="100.00",
            sort_order=2,
        )
        BonusProgram.objects.create(
            partner=self.partner,
            name="Кешбэк",
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00",
            max_redeem_share="20.00",
        )
        self._seed_bonuses("100.00")
        self.owner = User.objects.create_user(
            username="refund_owner", password="x", partner=self.partner, role=User.Role.OWNER
        )
        self._cart()
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, raw_items="PICK0002x1"
        )
        self.order = place_remote_order(
            partner_id=self.partner.id,
            telegram_id=TG,
            slot="now",
            use_bonuses=True,
            expected_total="500.00",
            expected_to_pay="400.00",
            now=NOON,
        )
        self.order = confirm_transfer(order=self.order, actor=self.cashier)
        self.bill = Bill.objects.get(bill_orders__order=self.order)
        self.burger = BillItem.objects.get(bill=self.bill, item_name="Бургер")
        self.juice = BillItem.objects.get(bill=self.bill, item_name="Сок")
        self.accrued = BonusTransaction.objects.get(
            order=self.order, transaction_type=BonusTransaction.TransactionType.ACCRUAL
        ).amount


class RefundFixture(RefundSetupMixin, TestCase):
    def _refund(self, items, actor=None, method=CASH):
        with self.captureOnCommitCallbacks(execute=True), patch(
            "apps.notifications.services.send_guest_refund_notice"
        ) as notice:
            refund = refund_bill_lines(
                bill=self.bill,
                bill_item_ids=[item.id for item in items],
                actor=actor or self.owner,
                reason_code=REASON,
                method=method,
            )
        self.notice = notice
        self.bill.refresh_from_db()
        self.order.refresh_from_db()
        self.guest.refresh_from_db()
        return refund


class PartialAndFullRefundTests(RefundFixture):
    def test_setup_is_a_paid_bill_with_bonuses_and_cashback(self):
        self.assertEqual(self.bill.status, Bill.Status.PAID)
        self.assertEqual(self.bill.paid_amount, Decimal("400.00"))
        self.assertEqual(self.bill.bonus_spent_amount, Decimal("100.00"))
        self.assertGreater(self.accrued, 0)

    def test_partial_refund_returns_its_share_of_money_bonuses_and_cashback(self):
        self.guest.refresh_from_db()
        balance_before = self.guest.loyalty_balance
        refund = self._refund([self.juice])
        # The juice is 100 of 500: a fifth of the money and of the bonuses.
        self.assertEqual(refund.money_amount, Decimal("80.00"))
        self.assertEqual(refund.bonus_returned, Decimal("20.00"))
        self.assertEqual(refund.cashback_revoked, (self.accrued / 5).quantize(Decimal("0.01")))
        self.assertFalse(refund.is_full)
        self.assertEqual(self.bill.refunded_amount, Decimal("80.00"))
        self.assertEqual(self.bill.status, Bill.Status.PAID)
        self.assertEqual(RefundLine.objects.get(refund=refund).bill_item, self.juice)
        self.assertEqual(
            self.guest.loyalty_balance,
            balance_before + refund.bonus_returned - refund.cashback_revoked,
        )
        self.assertEqual(self.order.status, Order.Status.ACCEPTED)
        self.notice.assert_called_once()

    def test_refunding_the_rest_takes_the_remainders_and_cancels_the_order(self):
        first = self._refund([self.juice])
        second = self._refund([self.burger])
        self.assertEqual(first.money_amount + second.money_amount, Decimal("400.00"))
        self.assertEqual(first.bonus_returned + second.bonus_returned, Decimal("100.00"))
        self.assertEqual(first.cashback_revoked + second.cashback_revoked, self.accrued)
        self.assertTrue(second.is_full)
        self.assertEqual(self.bill.refunded_amount, Decimal("400.00"))
        self.assertEqual(self.order.status, Order.Status.CANCELED)

    def test_full_refund_at_once(self):
        refund = self._refund([self.burger, self.juice], method=Refund.Method.TRANSFER)
        self.assertTrue(refund.is_full)
        self.assertEqual(refund.money_amount, Decimal("400.00"))
        self.assertEqual(refund.bonus_returned, Decimal("100.00"))
        self.assertEqual(self.order.status, Order.Status.CANCELED)

    def test_completed_order_stays_completed(self):
        transition_order_status(order=self.order, to_status=Order.Status.READY)
        transition_order_status(order=self.order, to_status=Order.Status.COMPLETED)
        self._refund([self.burger, self.juice])
        self.assertEqual(self.order.status, Order.Status.COMPLETED)


class RefundRulesTests(RefundFixture):
    def test_a_line_is_refunded_only_once(self):
        self._refund([self.juice])
        with self.assertRaisesMessage(BillingServiceError, "уже возвращ"):
            self._refund([self.juice])
        self.assertEqual(Refund.objects.count(), 1)

    def test_staff_without_the_bill_right_cannot_refund(self):
        with self.assertRaisesMessage(BillingServiceError, "прав"):
            self._refund([self.juice], actor=self.cashier)
        EmployeeProfile.objects.filter(user=self.cashier).update(can_adjust_bills=True)
        self._refund([self.juice], actor=self.cashier)
        self.assertEqual(Refund.objects.count(), 1)

    def test_only_a_paid_bill_is_refunded(self):
        Bill.objects.filter(id=self.bill.id).update(status=Bill.Status.PARTIALLY_PAID)
        with self.assertRaisesMessage(BillingServiceError, "оплаченн"):
            self._refund([self.juice])

    def test_lines_of_another_bill_or_partner_are_refused(self):
        other = Partner.objects.create(name="O", slug="o-refund", status=Partner.Status.ACTIVE)
        with self.assertRaisesMessage(BillingServiceError, "не из этого счёта"):
            refund_bill_lines(
                bill=self.bill,
                bill_item_ids=[self.juice.id, "00000000-0000-0000-0000-000000000000"],
                actor=self.owner,
                reason_code=REASON,
                method=CASH,
            )
        self.assertFalse(Refund.objects.exists())
        self.assertIsNotNone(other)

    def test_unknown_reason_or_method_is_refused(self):
        with self.assertRaises(BillingServiceError):
            refund_bill_lines(
                bill=self.bill,
                bill_item_ids=[self.juice.id],
                actor=self.owner,
                reason_code="nope",
                method=CASH,
            )
        with self.assertRaises(BillingServiceError):
            refund_bill_lines(
                bill=self.bill,
                bill_item_ids=[self.juice.id],
                actor=self.owner,
                reason_code=REASON,
                method="nope",
            )

    def test_spent_cashback_is_not_taken_below_zero(self):
        # A second order paid with money only; then the guest spends every bonus.
        self._cart()
        order = place_remote_order(
            partner_id=self.partner.id,
            telegram_id=TG,
            slot="now",
            use_bonuses=False,
            expected_total="400.00",
            expected_to_pay="400.00",
            now=NOON,
        )
        confirm_transfer(order=order, actor=self.cashier)
        bill = Bill.objects.get(bill_orders__order=order)
        accrued = BonusTransaction.objects.get(
            order=order, transaction_type=BonusTransaction.TransactionType.ACCRUAL
        ).amount
        self.guest.refresh_from_db()
        BonusTransaction.objects.create(
            partner=self.partner,
            guest=self.guest,
            transaction_type=BonusTransaction.TransactionType.REDEMPTION,
            amount=-self.guest.loyalty_balance,
        )
        from apps.bonuses.services import sync_bonus_balance_from_ledger

        sync_bonus_balance_from_ledger(self.guest)
        with self.captureOnCommitCallbacks(execute=True), patch(
            "apps.notifications.services.send_guest_refund_notice"
        ):
            refund = refund_bill_lines(
                bill=bill,
                bill_item_ids=list(bill.items.values_list("id", flat=True)),
                actor=self.owner,
                reason_code=REASON,
                method=CASH,
            )
        self.guest.refresh_from_db()
        self.assertEqual(refund.cashback_revoked, Decimal("0.00"))
        self.assertEqual(refund.cashback_not_revoked, accrued)
        self.assertEqual(self.guest.loyalty_balance, Decimal("0.00"))

    def test_daily_report_counts_refunds(self):
        refund = self._refund([self.juice])
        report = build_daily_operations_report(
            partner_id=self.partner.id,
            # The report day is the venue's day, not the UTC date.
            bucket_date=refund.created_at.astimezone(ZoneInfo(self.partner.timezone)).date(),
        )
        self.assertEqual(report.refunds_total, Decimal("80.00"))
        self.assertEqual(
            report.total_revenue,
            report.bill_revenue + report.walk_in_revenue - Decimal("80.00"),
        )


class RefundNoticeTests(RefundFixture):
    def test_guest_message_names_the_lines_money_bonuses_and_reason(self):
        from apps.notifications.services import render_guest_refund_notice

        refund = self._refund([self.juice], method=Refund.Method.TRANSFER)
        text = render_guest_refund_notice(refund)
        self.assertIn(f"Заказ #{self.order.public_id}: Сок × 1", text)
        self.assertIn("Деньги: 80.00 грн — переводом на карту.", text)
        self.assertIn("Бонусы вернулись: 20.00.", text)
        self.assertIn("Причина: Нет в наличии.", text)
        self.notice.assert_called_once()


class RefundAdminActionTests(RefundFixture):
    def test_owner_refunds_selected_lines_from_admin(self):
        from django.contrib import admin
        from django.test import RequestFactory

        from apps.billing.admin import BillItemAdmin

        model_admin = BillItemAdmin(BillItem, admin.site)
        request = RequestFactory().post(
            "/admin/",
            {"apply": "1", "reason_code": "quality", "method": "transfer", "reason_text": ""},
        )
        request.user = self.owner
        with patch.object(model_admin, "message_user") as message, patch(
            "apps.notifications.services.send_guest_refund_notice"
        ):
            response = model_admin.refund_selected_lines(
                request, BillItem.objects.filter(id=self.juice.id)
            )
        self.assertIsNone(response)
        refund = Refund.objects.get()
        self.assertEqual(refund.money_amount, Decimal("80.00"))
        self.assertEqual(refund.method, Refund.Method.TRANSFER)
        self.assertIn("возвращено 80.00 грн", message.call_args.args[1])

    def test_admin_shows_the_reason_page_first(self):
        from django.contrib import admin
        from django.test import RequestFactory

        from apps.billing.admin import BillItemAdmin

        model_admin = BillItemAdmin(BillItem, admin.site)
        request = RequestFactory().post("/admin/", {})
        request.user = self.owner
        response = model_admin.refund_selected_lines(
            request, BillItem.objects.filter(id=self.juice.id)
        )
        self.assertEqual(response.template_name, "admin/billing/adjustment_reason.html")
        self.assertFalse(Refund.objects.exists())
