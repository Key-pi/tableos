"""Unpaid remote orders: a limit per guest and auto-cancel (AA-037, PQ-036)."""

from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.billing.models import Bill, BillAdjustment
from apps.billing.remote_orders import (
    RemoteOrderError,
    cancel_expired_transfer_orders,
    confirm_transfer,
    reject_transfer,
    save_guest_phone,
)
from apps.billing.test_remote_orders import NOON, PickupFixtureMixin
from apps.orders.models import Order
from apps.partners.models import Partner, PartnerBotSettings


class PhoneMixin(PickupFixtureMixin):
    def setUp(self):
        super().setUp()
        save_guest_phone(partner_id=self.partner.id, telegram_id=7300001, phone="+380501112233")


class UnpaidLimitTests(PhoneMixin, TestCase):
    def _order(self):
        self._cart()
        return self._place()

    def test_defaults_are_two_orders_and_sixty_minutes(self):
        self.assertEqual(self.settings.remote_unpaid_orders_limit, 2)
        self.assertEqual(self.settings.transfer_wait_minutes, 60)

    def test_third_unpaid_order_is_refused(self):
        self._order()
        self._order()
        self._cart()
        with self.assertRaisesMessage(RemoteOrderError, "неоплаченные заказы"):
            self._place()
        self.assertEqual(Order.objects.count(), 2)

    def test_paid_and_canceled_orders_do_not_count(self):
        first = self._order()
        confirm_transfer(order=first, actor=self.cashier)
        second = self._order()
        reject_transfer(order=second, actor=self.cashier)
        self._order()
        self._order()
        self.assertEqual(Order.objects.count(), 4)

    def test_zero_means_no_limit(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            remote_unpaid_orders_limit=0
        )
        for _ in range(3):
            self._order()
        self.assertEqual(Order.objects.count(), 3)


class TransferAutoCancelTests(PhoneMixin, TestCase):
    def setUp(self):
        super().setUp()
        self._cart()
        self.order = self._place()

    def _age(self, minutes):
        Order.objects.filter(id=self.order.id).update(
            created_at=NOON - timedelta(minutes=minutes)
        )

    def test_unconfirmed_transfer_is_canceled_after_the_wait(self):
        self._age(61)
        self.assertEqual(cancel_expired_transfer_orders(now=NOON), 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELED)
        adjustment = BillAdjustment.objects.get()
        self.assertEqual(adjustment.bill.status, Bill.Status.CANCELED)
        self.assertEqual(adjustment.reason_code, BillAdjustment.ReasonCode.PAYMENT_NOT_RECEIVED)
        self.assertIsNone(adjustment.actor_id)
        self.assertIn("60", adjustment.reason_text)
        # A second run changes nothing.
        self.assertEqual(cancel_expired_transfer_orders(now=NOON), 0)

    def test_young_confirmed_or_paid_on_receipt_orders_stay(self):
        self._age(59)
        self.assertEqual(cancel_expired_transfer_orders(now=NOON), 0)
        self._age(120)
        confirm_transfer(order=self.order, actor=self.cashier)
        self.assertEqual(cancel_expired_transfer_orders(now=NOON), 0)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.ACCEPTED)

    def test_zero_minutes_turns_auto_cancel_off(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(transfer_wait_minutes=0)
        self._age(600)
        self.assertEqual(cancel_expired_transfer_orders(now=NOON), 0)

    def test_suspended_partner_is_left_alone(self):
        Partner.objects.filter(id=self.partner.id).update(status=Partner.Status.SUSPENDED)
        self._age(600)
        self.assertEqual(cancel_expired_transfer_orders(now=NOON), 0)

    def test_owner_cannot_set_a_negative_or_tiny_wait(self):
        self.settings.transfer_wait_minutes = 5
        with self.assertRaises(ValidationError) as caught:
            self.settings.full_clean()
        self.assertIn("transfer_wait_minutes", caught.exception.message_dict)
