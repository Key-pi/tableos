"""Payment options per remote module: transfer in advance and/or on receipt (PQ-033)."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.billing.models import Bill, BillAdjustment, Payment
from apps.billing.remote_orders import (
    RemoteOrderError,
    awaits_transfer_check,
    cancel_unpaid_remote_order,
    confirm_transfer,
    get_remote_quote,
    payment_modes_for,
    place_remote_order,
    record_payment_on_receipt,
    save_guest_phone,
)
from apps.billing.test_remote_orders import NOON, TG, PickupFixtureMixin
from apps.bonuses.models import BonusProgram
from apps.notifications.models import StaffNotification
from apps.notifications.services import _remote_status_note
from apps.orders.models import Order
from apps.orders.services import transition_order_status
from apps.partners.models import Partner, PartnerBotSettings
from apps.users.models import User

PREPAID = Order.PaymentTiming.PREPAID
ON_RECEIPT = Order.PaymentTiming.ON_RECEIPT


class PaymentModesFixture(PickupFixtureMixin):
    def setUp(self):
        super().setUp()
        save_guest_phone(partner_id=self.partner.id, telegram_id=TG, phone="+380501112233")
        self._cart()

    def _modes(self, *, transfer: bool, on_receipt: bool, details: str | None = None):
        update = {"pickup_pay_transfer": transfer, "pickup_pay_on_receipt": on_receipt}
        if details is not None:
            update["transfer_payment_details"] = details
        PartnerBotSettings.objects.filter(id=self.settings.id).update(**update)
        self.settings.refresh_from_db()

    def _place_with(self, payment, *, use_bonuses=False, to_pay="400.00"):
        return place_remote_order(
            partner_id=self.partner.id,
            telegram_id=TG,
            slot="now",
            use_bonuses=use_bonuses,
            expected_total="400.00",
            expected_to_pay=to_pay,
            payment=payment,
            now=NOON,
        )

    def _with_bonuses(self):
        self._seed_bonuses("100.00")
        BonusProgram.objects.create(
            partner=self.partner,
            name="Cashback",
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00",
            max_redeem_share="20.00",
        )


class PaymentOptionsTests(PaymentModesFixture, TestCase):
    def test_by_default_only_transfer_in_advance(self):
        self.assertEqual(payment_modes_for(self.settings, Order.Channel.PICKUP), (PREPAID,))
        order = self._place_with(PREPAID)
        self.assertEqual(order.payment_timing, PREPAID)
        self.assertTrue(awaits_transfer_check(order))

    def test_quote_lists_the_enabled_options(self):
        self._modes(transfer=True, on_receipt=True)
        quote = get_remote_quote(partner_id=self.partner.id, telegram_id=TG, use_bonuses=False)
        self.assertEqual(quote.payment_modes, (PREPAID, ON_RECEIPT))

    def test_on_receipt_needs_no_transfer_details(self):
        self._modes(transfer=False, on_receipt=True, details="")
        quote = get_remote_quote(partner_id=self.partner.id, telegram_id=TG, use_bonuses=False)
        self.assertEqual(quote.payment_modes, (ON_RECEIPT,))
        order = self._place_with(ON_RECEIPT)
        self.assertEqual(order.payment_timing, ON_RECEIPT)
        self.assertFalse(awaits_transfer_check(order))

    def test_an_option_the_owner_did_not_enable_is_refused(self):
        with self.assertRaisesMessage(RemoteOrderError, "способ оплаты"):
            self._place_with(ON_RECEIPT)
        self._modes(transfer=False, on_receipt=True)
        with self.assertRaisesMessage(RemoteOrderError, "способ оплаты"):
            self._place_with(PREPAID)
        self.assertFalse(Order.objects.exists())

    def test_on_receipt_order_goes_into_work_without_a_payment_check(self):
        self._modes(transfer=False, on_receipt=True)
        order = self._place_with(ON_RECEIPT)
        note = StaffNotification.objects.get(order=order).message
        self.assertIn("Оплата при получении: 400.00 грн", note)
        self.assertNotIn("перевод", note.lower())
        order = transition_order_status(order=order, to_status=Order.Status.ACCEPTED)
        self.assertEqual(order.status, Order.Status.ACCEPTED)
        self.assertIsNone(order.paid_at)

    def test_guest_messages_follow_the_payment_option(self):
        self._modes(transfer=False, on_receipt=True)
        order = self._place_with(ON_RECEIPT)
        self.assertEqual(
            _remote_status_note(order, Order.Status.ACCEPTED), "Заказ принят — готовим."
        )
        self.assertEqual(_remote_status_note(order, Order.Status.CANCELED), "")
        order.payment_timing = PREPAID
        self.assertIn("Оплата подтверждена", _remote_status_note(order, Order.Status.ACCEPTED))


class PaymentOnReceiptTests(PaymentModesFixture, TestCase):
    def setUp(self):
        super().setUp()
        self._modes(transfer=True, on_receipt=True)

    def test_staff_record_cash_or_card_on_receipt(self):
        order = self._place_with(ON_RECEIPT)
        order = record_payment_on_receipt(
            order=order, actor=self.cashier, method=Payment.Method.TERMINAL
        )
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(order.payment_method, Order.PaymentMethod.TERMINAL)
        bill = self._bill(order)
        self.assertEqual(bill.status, Bill.Status.PAID)
        self.assertEqual(bill.payments.get().amount, Decimal("400.00"))
        with self.assertRaisesMessage(RemoteOrderError, "уже оплачен"):
            record_payment_on_receipt(order=order, actor=self.cashier, method=Payment.Method.CASH)

    def test_bonuses_reduce_what_is_paid_on_receipt(self):
        self._with_bonuses()
        order = self._place_with(ON_RECEIPT, use_bonuses=True, to_pay="320.00")
        order = record_payment_on_receipt(
            order=order, actor=self.cashier, method=Payment.Method.CASH
        )
        payment = self._bill(order).payments.get(method=Payment.Method.CASH)
        self.assertEqual(payment.amount, Decimal("320.00"))

    def test_only_cash_handling_roles_of_the_partner_record_it(self):
        order = self._place_with(ON_RECEIPT)
        waiter = User.objects.create_user(
            username="receipt_waiter", password="x", partner=self.partner, role=User.Role.WAITER
        )
        with self.assertRaisesMessage(RemoteOrderError, "владелец, менеджер или кассир"):
            record_payment_on_receipt(order=order, actor=waiter, method=Payment.Method.CASH)
        other = Partner.objects.create(name="Other", slug="other-receipt")
        foreign = User.objects.create_user(
            username="receipt_foreign", password="x", partner=other, role=User.Role.CASHIER
        )
        with self.assertRaises(RemoteOrderError):
            record_payment_on_receipt(order=order, actor=foreign, method=Payment.Method.CASH)
        self.assertIsNone(self._bill(order).payments.first())

    def test_only_cash_or_card_are_accepted_on_receipt(self):
        order = self._place_with(ON_RECEIPT)
        with self.assertRaisesMessage(RemoteOrderError, "способ"):
            record_payment_on_receipt(
                order=order, actor=self.cashier, method=Payment.Method.TRANSFER
            )

    def test_prepaid_order_is_not_paid_on_receipt(self):
        order = self._place_with(PREPAID)
        with self.assertRaisesMessage(RemoteOrderError, "переводом"):
            record_payment_on_receipt(order=order, actor=self.cashier, method=Payment.Method.CASH)
        with self.assertRaises(RemoteOrderError):
            cancel_unpaid_remote_order(order=order, actor=self.cashier)

    def test_on_receipt_order_has_no_transfer_to_confirm(self):
        order = self._place_with(ON_RECEIPT)
        with self.assertRaises(RemoteOrderError):
            confirm_transfer(order=order, actor=self.cashier)

    def test_refusal_cancels_the_order_and_returns_bonuses(self):
        self._with_bonuses()
        order = self._place_with(ON_RECEIPT, use_bonuses=True, to_pay="320.00")
        order = transition_order_status(order=order, to_status=Order.Status.ACCEPTED)
        bill = self._bill(order)
        order = cancel_unpaid_remote_order(order=order, actor=self.cashier, comment="не пришёл")

        self.assertEqual(order.status, Order.Status.CANCELED)
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.CANCELED)
        adjustment = BillAdjustment.objects.get(bill=bill)
        self.assertEqual(adjustment.reason_code, BillAdjustment.ReasonCode.GUEST_REFUSED)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.loyalty_balance, Decimal("100.00"))

    def test_paid_order_is_not_cancelled_as_a_refusal(self):
        order = self._place_with(ON_RECEIPT)
        record_payment_on_receipt(order=order, actor=self.cashier, method=Payment.Method.CASH)
        with self.assertRaisesMessage(RemoteOrderError, "оплат"):
            cancel_unpaid_remote_order(order=order, actor=self.cashier)


class PaymentSettingsValidationTests(TestCase):
    def _settings(self, **fields):
        partner = Partner.objects.create(name="Pay Settings", slug="pay-settings")
        return PartnerBotSettings(
            partner=partner,
            module_menu_enabled=True,
            module_orders_enabled=True,
            module_cart_enabled=True,
            **fields,
        )

    def test_enabled_module_needs_at_least_one_option(self):
        settings = self._settings(
            module_pickup_enabled=True,
            pickup_pay_transfer=False,
            pickup_pay_on_receipt=False,
        )
        with self.assertRaises(ValidationError) as caught:
            settings.full_clean()
        self.assertIn("pickup_pay_transfer", caught.exception.message_dict)

    def test_transfer_option_needs_details(self):
        settings = self._settings(module_delivery_enabled=True, delivery_pay_transfer=True)
        with self.assertRaises(ValidationError) as caught:
            settings.full_clean()
        self.assertIn("transfer_payment_details", caught.exception.message_dict)

    def test_on_receipt_only_needs_no_details(self):
        settings = self._settings(
            module_pickup_enabled=True,
            pickup_pay_transfer=False,
            pickup_pay_on_receipt=True,
        )
        settings.full_clean()


class ExistingRemoteOrdersMigrationTests(PaymentModesFixture, TestCase):
    def test_orders_placed_before_the_options_were_paid_in_advance(self):
        from importlib import import_module

        from django.apps import apps as django_apps

        migration = import_module("apps.orders.migrations.0010_payment_timing")
        order = self._place_with(PREPAID)
        Order.objects.filter(id=order.id).update(payment_timing="")
        migration.mark_existing_remote_orders_prepaid(django_apps, None)
        order.refresh_from_db()
        self.assertEqual(order.payment_timing, PREPAID)
