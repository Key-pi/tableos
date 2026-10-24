"""Shipping by post (docs/architecture/SHIPPING.md, PQ-034)."""

from __future__ import annotations

from datetime import time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.billing.models import Bill, Payment
from apps.billing.remote_orders import (
    RemoteOrderError,
    get_remote_quote,
    get_shipping_terms,
    mark_order_shipped,
    place_remote_order,
    record_payment_on_receipt,
    save_guest_phone,
    save_guest_shipping_carrier,
    save_guest_shipping_destination,
    tracking_link,
)
from apps.billing.test_remote_orders import NOON
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.notifications.models import StaffNotification
from apps.notifications.services import _remote_status_note
from apps.orders.models import Order, OrderItem
from apps.orders.services import (
    OrderFlowError,
    add_items_to_cart_for_telegram_user,
    switch_cart_channel_for_telegram_user,
    transition_order_status,
)
from apps.partners.models import BotInstance, OpeningHours, Partner, PartnerBotSettings
from apps.users.models import TelegramAccount, User
from apps.users.services import get_or_create_guest_profile
from core.security.crypto import encrypt_text

TG = 7600001
NP = Order.Carrier.NOVA_POSHTA
CARRIER_COST = Order.ShippingCost.CARRIER
FIXED_COST = Order.ShippingCost.FIXED


class ShippingFixture(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Shipping Venue",
            slug="shipping-venue",
            status=Partner.Status.ACTIVE,
            timezone="Europe/Kyiv",
        )
        self.settings = PartnerBotSettings.objects.create(
            partner=self.partner,
            module_menu_enabled=True,
            module_orders_enabled=True,
            module_cart_enabled=True,
            module_tables_enabled=False,
            module_shipping_enabled=True,
            transfer_payment_details="Карта 4149 0000 0000 0000",
            shipping_nova_poshta=True,
            shipping_ukrposhta=True,
            shipping_cost_carrier=True,
            shipping_cost_fixed=True,
            shipping_fee=Decimal("70.00"),
            shipping_free_from=Decimal("1000.00"),
        )
        category = MenuCategory.objects.create(partner=self.partner, name="Кофе")
        self.item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="SHIP0001",
            name="Кофе в зёрнах",
            price="300.00",
        )
        self.guest = get_or_create_guest_profile(partner_id=self.partner.id, telegram_id=TG)
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, channel=Order.Channel.SHIPPING
        )
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, raw_items="SHIP0001x2"
        )
        save_guest_phone(partner_id=self.partner.id, telegram_id=TG, phone="+380501112233")
        self.cashier = User.objects.create_user(
            username="ship_cashier", password="x", partner=self.partner, role=User.Role.CASHIER
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.cashier,
            telegram_account=TelegramAccount.objects.create(telegram_id=7600901),
            bot_notifications_enabled=True,
        )

    def _destination(self, carrier=NP, text="Іваненко Іван, Київ, відділення 25"):
        save_guest_shipping_carrier(partner_id=self.partner.id, telegram_id=TG, carrier=carrier)
        save_guest_shipping_destination(partner_id=self.partner.id, telegram_id=TG, text=text)

    def _place(self, *, cost=CARRIER_COST, total="600.00", to_pay="600.00", payment=None):
        return place_remote_order(
            partner_id=self.partner.id,
            telegram_id=TG,
            slot="now",
            use_bonuses=False,
            expected_total=total,
            expected_to_pay=to_pay,
            channel=Order.Channel.SHIPPING,
            payment=payment or Order.PaymentTiming.PREPAID,
            shipping_cost=cost,
            now=NOON,
        )


class ShippingTermsTests(ShippingFixture):
    def test_terms_list_carriers_and_postage_options(self):
        text = get_shipping_terms(self.partner.id).describe()
        self.assertIn("Нова Пошта, Укрпошта", text)
        self.assertIn("по тарифу перевозчика", text)
        self.assertIn("70.00 грн", text)
        self.assertIn("бесплатно от 1000.00 грн", text)

    def test_only_enabled_carriers_are_saved(self):
        with self.assertRaisesMessage(RemoteOrderError, "служб"):
            save_guest_shipping_carrier(
                partner_id=self.partner.id, telegram_id=TG, carrier=Order.Carrier.MEEST
            )

    def test_destination_is_cleaned_and_required(self):
        with self.assertRaises(RemoteOrderError):
            save_guest_shipping_destination(partner_id=self.partner.id, telegram_id=TG, text="  ")
        self._destination(text="  Іваненко   Іван,\nКиїв, відділення 25 ")
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.shipping_destination, "Іваненко Іван, Київ, відділення 25")
        self.assertEqual(self.guest.shipping_carrier, NP)


class ShippingOrderTests(ShippingFixture):
    def test_postage_by_carrier_tariff_adds_no_line(self):
        self._destination()
        quote = get_remote_quote(
            partner_id=self.partner.id,
            telegram_id=TG,
            use_bonuses=False,
            channel=Order.Channel.SHIPPING,
            shipping_cost=CARRIER_COST,
        )
        self.assertEqual(quote.total, Decimal("600.00"))
        self.assertEqual(quote.shipping_cost_options, (CARRIER_COST, FIXED_COST))
        order = self._place()
        self.assertEqual(order.channel, Order.Channel.SHIPPING)
        self.assertEqual(order.shipping_carrier, NP)
        self.assertEqual(order.shipping_destination, "Іваненко Іван, Київ, відділення 25")
        self.assertEqual(order.shipping_cost, CARRIER_COST)
        self.assertIsNone(order.requested_for)
        self.assertFalse(order.items.filter(kind=OrderItem.Kind.DELIVERY_FEE).exists())
        note = StaffNotification.objects.get(order=order).message
        self.assertIn("Нова Пошта", note)
        self.assertIn("відділення 25", note)
        self.assertIn("по тарифу перевозчика", note)

    def test_fixed_postage_is_an_order_line_free_from_the_threshold(self):
        self._destination()
        order = self._place(cost=FIXED_COST, total="670.00", to_pay="670.00")
        line = order.items.get(kind=OrderItem.Kind.DELIVERY_FEE)
        self.assertEqual(line.item_name, "Пересылка")
        self.assertEqual(line.unit_price, Decimal("70.00"))

        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, raw_items="SHIP0001x4"
        )
        order = self._place(cost=FIXED_COST, total="1200.00", to_pay="1200.00")
        self.assertFalse(order.items.filter(kind=OrderItem.Kind.DELIVERY_FEE).exists())

    def test_postage_option_the_owner_did_not_enable_is_refused(self):
        self._destination()
        PartnerBotSettings.objects.filter(id=self.settings.id).update(shipping_cost_fixed=False)
        with self.assertRaisesMessage(RemoteOrderError, "пересылк"):
            self._place(cost=FIXED_COST, total="670.00", to_pay="670.00")

    def test_destination_and_an_enabled_carrier_are_required(self):
        with self.assertRaisesMessage(RemoteOrderError, "Куда отправить"):
            self._place()
        self._destination()
        PartnerBotSettings.objects.filter(id=self.settings.id).update(shipping_nova_poshta=False)
        with self.assertRaisesMessage(RemoteOrderError, "служб"):
            self._place()

    def test_orders_are_accepted_outside_opening_hours(self):
        OpeningHours.objects.create(
            partner=self.partner, weekday=2, opens_at=time(18, 0), closes_at=time(23, 0)
        )
        self._destination()
        self.assertEqual(self._place().channel, Order.Channel.SHIPPING)

    def test_minimum_order_applies(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            shipping_min_order=Decimal("1000.00")
        )
        self._destination()
        with self.assertRaisesMessage(RemoteOrderError, "Минимальная сумма заказа на отправку"):
            self._place()

    def test_items_not_sold_for_shipping_are_refused(self):
        MenuItem.objects.filter(id=self.item.id).update(available_for_shipping=False)
        self._destination()
        with self.assertRaisesMessage(RemoteOrderError, "Недоступно"):
            self._place()


class CashOnDeliveryTests(ShippingFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(id=self.settings.id).update(shipping_pay_on_receipt=True)
        self._destination()
        self.order = self._place(payment=Order.PaymentTiming.ON_RECEIPT)

    def test_cash_on_delivery_is_recorded_when_the_money_arrives(self):
        with self.assertRaisesMessage(RemoteOrderError, "наложенн"):
            record_payment_on_receipt(
                order=self.order, actor=self.cashier, method=Payment.Method.CASH
            )
        order = record_payment_on_receipt(
            order=self.order, actor=self.cashier, method=Payment.Method.COD
        )
        self.assertEqual(order.payment_method, Order.PaymentMethod.COD)
        self.assertEqual(Bill.objects.get(status=Bill.Status.PAID).payments.get().amount, 600)

    def test_staff_note_mentions_cash_on_delivery(self):
        note = StaffNotification.objects.get(order=self.order).message
        self.assertIn("Наложенный платёж: 600.00 грн", note)


class ShippedTests(ShippingFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(id=self.settings.id).update(shipping_pay_on_receipt=True)
        self._destination()
        self.order = self._place(payment=Order.PaymentTiming.ON_RECEIPT)
        self.order = transition_order_status(order=self.order, to_status=Order.Status.ACCEPTED)

    def test_tracking_number_is_entered_once_the_parcel_is_packed(self):
        with self.assertRaisesMessage(RemoteOrderError, "Сначала отметьте"):
            mark_order_shipped(
                order=self.order, actor=self.cashier, tracking_number="20450000000001"
            )
        transition_order_status(order=self.order, to_status=Order.Status.READY)
        order = mark_order_shipped(
            order=self.order, actor=self.cashier, tracking_number=" 2045 0000 0000 01 "
        )
        self.assertEqual(order.status, Order.Status.DELIVERING)
        self.assertEqual(order.tracking_number, "20450000000001")
        note = _remote_status_note(order, Order.Status.DELIVERING)
        self.assertIn("20450000000001", note)
        self.assertIn(tracking_link(NP, "20450000000001"), note)

    def test_bad_number_or_foreign_actor_is_refused(self):
        transition_order_status(order=self.order, to_status=Order.Status.READY)
        with self.assertRaisesMessage(RemoteOrderError, "ТТН"):
            mark_order_shipped(order=self.order, actor=self.cashier, tracking_number="<b>1</b>")
        other = Partner.objects.create(name="Other", slug="other-ship")
        foreign = User.objects.create_user(
            username="ship_foreign", password="x", partner=other, role=User.Role.CASHIER
        )
        with self.assertRaises(RemoteOrderError):
            mark_order_shipped(order=self.order, actor=foreign, tracking_number="20450000000001")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.READY)

    def test_ready_shipping_order_is_not_shipped_without_a_number(self):
        order = transition_order_status(order=self.order, to_status=Order.Status.READY)
        with self.assertRaisesMessage(OrderFlowError, "ТТН"):
            transition_order_status(order=order, to_status=Order.Status.DELIVERING)


class ShippingSettingsValidationTests(TestCase):
    def _settings(self, **fields):
        partner = Partner.objects.create(name="Ship Settings", slug="ship-settings")
        return PartnerBotSettings(
            partner=partner,
            module_menu_enabled=True,
            module_orders_enabled=True,
            module_cart_enabled=True,
            module_tables_enabled=False,
            module_shipping_enabled=True,
            transfer_payment_details="Карта",
            **fields,
        )

    def _errors(self, settings):
        with self.assertRaises(ValidationError) as caught:
            settings.full_clean()
        return caught.exception.message_dict

    def test_needs_a_carrier_a_postage_option_and_a_payment_option(self):
        errors = self._errors(
            self._settings(
                shipping_nova_poshta=False,
                shipping_cost_carrier=False,
                shipping_pay_transfer=False,
            )
        )
        self.assertIn("shipping_nova_poshta", errors)
        self.assertIn("shipping_cost_carrier", errors)
        self.assertIn("shipping_pay_transfer", errors)

    def test_defaults_are_valid_and_shipping_alone_allows_orders(self):
        self._settings().full_clean()


class ShippedGuestMessageTests(ShippingFixture):
    def test_guest_gets_the_number_link_and_a_received_button(self):
        from unittest.mock import patch

        from apps.notifications.services import send_guest_order_status_update_now

        self._destination()
        order = self._place(payment=Order.PaymentTiming.PREPAID)
        Order.objects.filter(id=order.id).update(
            status=Order.Status.DELIVERING, tracking_number="20450000000001"
        )
        BotInstance.objects.create(
            partner=self.partner,
            display_name="Ship",
            username="ship_msg_bot",
            token_encrypted=encrypt_text("123456789:AAEexampleexampleexampleexample"),
        )
        sent = []

        async def capture(_token, messages, **_kwargs):
            sent.extend(messages)
            return [(message[0], True, "", False, False) for message in messages]

        with patch("apps.notifications.services._send_telegram_messages", new=capture):
            send_guest_order_status_update_now(
                order_id=str(order.id),
                from_status=Order.Status.READY,
                to_status=Order.Status.DELIVERING,
            )
        chat_id, text, markup = sent[0]
        self.assertEqual(chat_id, TG)
        self.assertIn("ТТН <code>20450000000001</code>", text)
        self.assertIn("novaposhta.ua/tracking/?cargo_number=20450000000001", text)
        button = markup.inline_keyboard[0][0]
        self.assertEqual(button.callback_data, f"guestorderconfirm:{order.public_id}")


class ModuleIndependenceTests(ShippingFixture):
    """Modules are switched on and set up separately and do not affect each other."""

    def setUp(self):
        super().setUp()
        self.settings.module_pickup_enabled = True
        self.settings.pickup_pay_transfer = True
        self.settings.pickup_pay_on_receipt = False
        self.settings.shipping_pay_on_receipt = True
        self.settings.shipping_min_order = Decimal("5000.00")
        self.settings.save()

    def _pickup_quote(self):
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, channel=Order.Channel.PICKUP
        )
        return get_remote_quote(
            partner_id=self.partner.id,
            telegram_id=TG,
            use_bonuses=False,
            channel=Order.Channel.PICKUP,
        )

    def test_shipping_settings_do_not_change_pickup(self):
        quote = self._pickup_quote()
        self.assertEqual(quote.total, Decimal("600.00"))
        self.assertEqual(quote.payment_modes, (Order.PaymentTiming.PREPAID,))

    def test_turning_shipping_off_stops_only_shipping(self):
        self.settings.module_shipping_enabled = False
        self.settings.save()
        with self.assertRaisesMessage(RemoteOrderError, "Отправка почтой сейчас отключена"):
            get_remote_quote(
                partner_id=self.partner.id,
                telegram_id=TG,
                use_bonuses=False,
                channel=Order.Channel.SHIPPING,
            )
        self.assertEqual(self._pickup_quote().total, Decimal("600.00"))

    def test_menu_marks_are_per_module(self):
        MenuItem.objects.filter(id=self.item.id).update(available_for_shipping=False)
        self.assertEqual(self._pickup_quote().items_total, Decimal("600.00"))


class GuestReceiptWithoutTableTests(ShippingFixture):
    def test_without_a_table_only_a_shipping_order_can_be_confirmed(self):
        from apps.orders.services import confirm_order_received_by_guest

        self.settings.module_pickup_enabled = True
        self.settings.save()
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, channel=Order.Channel.PICKUP
        )
        pickup = place_remote_order(
            partner_id=self.partner.id,
            telegram_id=TG,
            slot="now",
            use_bonuses=False,
            expected_total="600.00",
            expected_to_pay="600.00",
            channel=Order.Channel.PICKUP,
            payment=Order.PaymentTiming.PREPAID,
            now=NOON,
        )
        with self.assertRaises(OrderFlowError):
            confirm_order_received_by_guest(
                partner_id=self.partner.id, telegram_id=TG, order_public_id=pickup.public_id
            )
        pickup.refresh_from_db()
        self.assertEqual(pickup.status, Order.Status.NEW)
