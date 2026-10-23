"""Bill corrections: line removal, void and cancelling billed orders (B3.2 part A).

Owner decisions of 2026-10-23 (PQ-028 and the B3.2 design answers):
- only staff with the owner-granted permission (or the venue owner) may change
  or void a bill; every change is kept in BillAdjustment with a reason;
- the total may never drop below the money already paid;
- a bill with applied bonuses cannot be changed line by line, only voided,
  and voiding returns the bonuses to the guest;
- cancelling an order that is in an open bill needs the same permission and
  removes its lines; an order in a paid bill cannot be cancelled.
"""

from decimal import Decimal

from django.test import TestCase

from apps.billing.models import Bill, BillAdjustment, BillingRequest, BillItem, Payment
from apps.billing.services import (
    BillingServiceError,
    cancel_order,
    create_bill_from_orders,
    create_billing_request_for_telegram_user,
    pay_bill_with_bonuses,
    record_payment,
    remove_bill_lines,
    void_bill,
)
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.bonuses.services import get_bonus_ledger_balance
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Order
from apps.orders.services import OrderFlowError, create_order_from_session, transition_order_status
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, GuestProfile, User

REASON = BillAdjustment.ReasonCode.GUEST_REFUSED


class BillChangeTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Correction Venue",
            slug="correction-venue",
            status=Partner.Status.ACTIVE,
        )
        PartnerBotSettings.objects.create(partner=self.partner, module_billing_enabled=True)
        table = Table.objects.create(partner=self.partner, number=1, qr_token="correction01")
        category = MenuCategory.objects.create(partner=self.partner, name="Bar")
        self.menu_item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            name="Lemonade",
            price="150.00",
        )
        self.session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=7710001,
            payload=table.deep_link_payload,
            username="correction_guest",
        )
        self.order = self._order(quantity=2)
        self.second_order = self._order(quantity=1)

        self.owner = User.objects.create_user(
            username="correction_owner",
            password="pass12345",
            partner=self.partner,
            role=User.Role.OWNER,
        )
        self.granted = self._staff("granted_cashier", can_adjust_bills=True)
        self.not_granted = self._staff("plain_cashier", can_adjust_bills=False)

    def _order(self, *, quantity):
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

    def _staff(self, username, *, can_adjust_bills):
        user = User.objects.create_user(
            username=username,
            password="pass12345",
            partner=self.partner,
            role=User.Role.CASHIER,
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=user,
            can_adjust_bills=can_adjust_bills,
        )
        return user

    def _bill(self, *orders, kind=Bill.Kind.SHARED):
        return create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(order.id) for order in orders],
            kind=kind,
        )

    def _item_ids(self, order):
        return [str(item.id) for item in order.items.all()]

    # --- cancelling a billed order -------------------------------------------------

    def test_cancelled_order_is_no_longer_charged_in_its_open_bill(self):
        bill = self._bill(self.order, self.second_order)
        self.assertEqual(bill.total_amount, Decimal("450.00"))

        cancel_order(order=self.second_order, actor=self.granted, reason_code=REASON)

        bill.refresh_from_db()
        self.second_order.refresh_from_db()
        self.assertEqual(self.second_order.status, Order.Status.CANCELED)
        self.assertEqual(bill.total_amount, Decimal("300.00"))
        self.assertFalse(bill.bill_orders.filter(order=self.second_order).exists())
        adjustment = BillAdjustment.objects.get(bill=bill)
        self.assertEqual(adjustment.kind, BillAdjustment.Kind.ORDER_CANCELED)
        self.assertEqual(adjustment.actor, self.granted)
        self.assertEqual(adjustment.total_before, Decimal("450.00"))
        self.assertEqual(adjustment.total_after, Decimal("300.00"))

    def test_cancelling_billed_order_requires_permission(self):
        bill = self._bill(self.order)

        with self.assertRaises(BillingServiceError):
            cancel_order(order=self.order, actor=self.not_granted, reason_code=REASON)

        bill.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(bill.total_amount, Decimal("300.00"))
        self.assertNotEqual(self.order.status, Order.Status.CANCELED)

    def test_direct_transition_cannot_cancel_a_billed_order(self):
        self._bill(self.order)

        with self.assertRaises(OrderFlowError):
            transition_order_status(order=self.order, to_status=Order.Status.CANCELED)

    def test_order_in_paid_bill_cannot_be_cancelled(self):
        bill = self._bill(self.order)
        record_payment(bill=bill, amount=bill.total_amount, method=Payment.Method.CASH)

        with self.assertRaises(BillingServiceError):
            cancel_order(order=self.order, actor=self.owner, reason_code=REASON)

    def test_unbilled_order_cancels_without_bill_permission(self):
        cancel_order(order=self.second_order, actor=self.not_granted)

        self.second_order.refresh_from_db()
        self.assertEqual(self.second_order.status, Order.Status.CANCELED)

    # --- removing lines --------------------------------------------------------------

    def test_removed_line_is_kept_in_history_and_can_be_billed_again(self):
        bill = self._bill(self.order, self.second_order)

        remove_bill_lines(
            bill=bill,
            order_item_ids=self._item_ids(self.second_order),
            actor=self.owner,
            reason_code=BillAdjustment.ReasonCode.INPUT_ERROR,
            reason_text="wrong table",
        )

        bill.refresh_from_db()
        self.assertEqual(bill.total_amount, Decimal("300.00"))
        removed = BillItem.objects.get(bill=bill, order=self.second_order)
        self.assertFalse(removed.is_active)
        self.assertEqual(removed.removed_by, self.owner)
        adjustment = BillAdjustment.objects.get(bill=bill)
        self.assertEqual(adjustment.reason_text, "wrong table")
        self.assertEqual(adjustment.payload["order_item_ids"], self._item_ids(self.second_order))

        new_bill = self._bill(self.second_order)
        self.assertEqual(new_bill.total_amount, Decimal("150.00"))

    def test_removing_lines_requires_permission(self):
        bill = self._bill(self.order, self.second_order)

        with self.assertRaises(BillingServiceError):
            remove_bill_lines(
                bill=bill,
                order_item_ids=self._item_ids(self.second_order),
                actor=self.not_granted,
                reason_code=REASON,
            )
        self.assertFalse(BillAdjustment.objects.exists())

    def test_admin_partner_owner_may_change_bills(self):
        admin_owner = User.objects.create_user(
            username="admin_owner",
            password="pass12345",
            partner=self.partner,
            role=User.Role.MANAGER,
        )
        AdminAccessProfile.objects.create(
            user=admin_owner,
            partner=self.partner,
            preset=AdminAccessPreset.OWNER,
            is_partner_owner=True,
            can_access_admin=True,
        )
        bill = self._bill(self.order, self.second_order)

        remove_bill_lines(
            bill=bill,
            order_item_ids=self._item_ids(self.second_order),
            actor=admin_owner,
            reason_code=REASON,
        )

        bill.refresh_from_db()
        self.assertEqual(bill.total_amount, Decimal("300.00"))

    def test_total_cannot_drop_below_paid_amount(self):
        bill = self._bill(self.order, self.second_order)
        record_payment(bill=bill, amount="400.00", method=Payment.Method.CASH)

        with self.assertRaises(BillingServiceError):
            remove_bill_lines(
                bill=bill,
                order_item_ids=self._item_ids(self.second_order),
                actor=self.owner,
                reason_code=REASON,
            )

        bill.refresh_from_db()
        self.assertEqual(bill.total_amount, Decimal("450.00"))

    def test_removal_that_matches_paid_amount_settles_the_bill(self):
        bill = self._bill(self.order, self.second_order)
        record_payment(bill=bill, amount="300.00", method=Payment.Method.CASH)

        remove_bill_lines(
            bill=bill,
            order_item_ids=self._item_ids(self.second_order),
            actor=self.owner,
            reason_code=BillAdjustment.ReasonCode.COMPLIMENT,
        )

        bill.refresh_from_db()
        self.order.refresh_from_db()
        self.second_order.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.PAID)
        self.assertIsNotNone(self.order.paid_at)
        self.assertIsNone(self.second_order.paid_at)

    def test_bill_with_applied_bonuses_cannot_be_changed_line_by_line(self):
        self._give_bonuses("100.00")
        bill = self._bill(self.order, self.second_order, kind=Bill.Kind.PERSONAL)
        pay_bill_with_bonuses(bill=bill)

        with self.assertRaises(BillingServiceError):
            remove_bill_lines(
                bill=bill,
                order_item_ids=self._item_ids(self.second_order),
                actor=self.owner,
                reason_code=REASON,
            )
        with self.assertRaises(BillingServiceError):
            cancel_order(order=self.second_order, actor=self.owner, reason_code=REASON)

    # --- voiding ---------------------------------------------------------------------

    def test_void_releases_orders_and_reopens_requests(self):
        result = create_billing_request_for_telegram_user(
            partner_id=self.partner.id,
            telegram_id=7710001,
            request_type=BillingRequest.RequestType.SHARED,
        )
        bill = result.bill

        void_bill(bill=bill, actor=self.granted, reason_code=BillAdjustment.ReasonCode.OTHER)

        bill.refresh_from_db()
        request = result.request
        request.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.CANCELED)
        self.assertFalse(bill.items.filter(is_active=True).exists())
        self.assertEqual(request.status, BillingRequest.Status.OPEN)
        self.assertIsNone(request.bill_id)
        self.assertEqual(BillAdjustment.objects.get(bill=bill).kind, BillAdjustment.Kind.VOID)
        self.assertEqual(self._bill(self.order, self.second_order).total_amount, Decimal("450.00"))

    def test_void_returns_applied_bonuses_to_the_guest(self):
        self._give_bonuses("100.00")
        bill = self._bill(self.order, self.second_order, kind=Bill.Kind.PERSONAL)
        pay_bill_with_bonuses(bill=bill)
        guest = GuestProfile.objects.get(id=self.session.guest_id)
        self.assertEqual(guest.loyalty_balance, Decimal("0.00"))

        void_bill(bill=bill, actor=self.owner, reason_code=REASON)

        guest.refresh_from_db()
        self.assertEqual(guest.loyalty_balance, Decimal("100.00"))
        self.assertEqual(
            get_bonus_ledger_balance(partner_id=self.partner.id, guest_id=guest.id),
            Decimal("100.00"),
        )
        reversal = BonusTransaction.objects.get(
            bill=bill,
            transaction_type=BonusTransaction.TransactionType.REVERSAL,
        )
        self.assertEqual(reversal.amount, Decimal("100.00"))

    def test_bill_with_money_payment_cannot_be_voided(self):
        bill = self._bill(self.order)
        record_payment(bill=bill, amount="100.00", method=Payment.Method.CASH)

        with self.assertRaises(BillingServiceError):
            void_bill(bill=bill, actor=self.owner, reason_code=REASON)

    def test_void_requires_permission(self):
        bill = self._bill(self.order)

        with self.assertRaises(BillingServiceError):
            void_bill(bill=bill, actor=self.not_granted, reason_code=REASON)

        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.DRAFT)

    def _give_bonuses(self, amount):
        BonusProgram.objects.create(
            partner=self.partner,
            name="Full redeem",
            trigger_event=BonusProgram.TriggerEvent.MANUAL_PURCHASE,
            program_type=BonusProgram.ProgramType.CASHBACK,
            max_redeem_share="100.00",
        )
        guest = self.session.guest
        BonusTransaction.objects.create(
            partner=self.partner,
            guest=guest,
            transaction_type=BonusTransaction.TransactionType.MANUAL,
            amount=amount,
        )
        GuestProfile.objects.filter(id=guest.id).update(loyalty_balance=Decimal(amount))
