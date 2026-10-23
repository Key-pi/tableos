"""Delivery with prepayment by transfer (docs/architecture/DELIVERY.md, PQ-032)."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.billing.models import Bill
from apps.billing.remote_orders import (
    RemoteOrderError,
    confirm_transfer,
    delivery_fee_for,
    get_remote_quote,
    place_remote_order,
    save_guest_delivery_address,
    save_guest_delivery_location,
    save_guest_phone,
)
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.notifications.models import StaffNotification
from apps.notifications.services import _remote_status_note
from apps.orders.models import Cart, Order, OrderItem
from apps.orders.services import (
    OrderFlowError,
    add_items_to_cart_for_telegram_user,
    get_available_staff_actions,
    get_or_create_active_cart_for_telegram_user,
    switch_cart_channel_for_telegram_user,
    transition_order_status,
)
from apps.partners.models import Partner, PartnerBotSettings
from apps.users.models import GuestProfile, TelegramAccount, User
from apps.users.services import get_or_create_guest_profile

# Wednesday 2026-10-21 12:00 in Kyiv (UTC+3).
NOON = datetime(2026, 10, 21, 9, 0, tzinfo=dt_timezone.utc)
TG = 7400001
D = Order.Channel.DELIVERY


class DeliveryFixtureMixin:
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Delivery Venue",
            slug="delivery-venue",
            status=Partner.Status.ACTIVE,
            timezone="Europe/Kyiv",
        )
        self.settings = PartnerBotSettings.objects.create(
            partner=self.partner,
            module_menu_enabled=True,
            module_orders_enabled=True,
            module_cart_enabled=True,
            module_tables_enabled=False,
            module_delivery_enabled=True,
            transfer_payment_details="Карта 4149 0000 0000 0000, ФОП Тест",
            delivery_fee=Decimal("60.00"),
            delivery_free_from=Decimal("1000.00"),
            delivery_min_order=Decimal("150.00"),
        )
        category = MenuCategory.objects.create(partner=self.partner, name="Еда", sort_order=1)
        self.item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="DLV00001",
            name="Пицца",
            price="200.00",
            sort_order=1,
        )
        self.hookah = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="DLV00002",
            name="Кальян",
            price="500.00",
            sort_order=2,
            available_for_delivery=False,
        )
        self.guest = get_or_create_guest_profile(partner_id=self.partner.id, telegram_id=TG)
        self.cashier = User.objects.create_user(
            username="delivery_cashier",
            password="x",
            partner=self.partner,
            role=User.Role.CASHIER,
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.cashier,
            telegram_account=TelegramAccount.objects.create(telegram_id=7400901),
            bot_notifications_enabled=True,
        )

    def _cart(self, quantity=2):
        switch_cart_channel_for_telegram_user(partner_id=self.partner.id, telegram_id=TG, channel=D)
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id,
            telegram_id=TG,
            raw_items=f"DLV00001x{quantity}",
        )

    def _ready_guest(self, address="ул. Крещатик 1, кв. 5"):
        save_guest_phone(partner_id=self.partner.id, telegram_id=TG, phone="+380671234567")
        save_guest_delivery_address(partner_id=self.partner.id, telegram_id=TG, address=address)

    def _place(self, *, total="460.00", to_pay="460.00", use_bonuses=False, slot="now"):
        return place_remote_order(
            partner_id=self.partner.id,
            telegram_id=TG,
            slot=slot,
            use_bonuses=use_bonuses,
            expected_total=total,
            expected_to_pay=to_pay,
            channel=D,
            now=NOON,
        )


class DeliverySettingsTests(DeliveryFixtureMixin, TestCase):
    def test_delivery_needs_transfer_details_and_sane_amounts(self):
        self.settings.transfer_payment_details = ""
        with self.assertRaises(ValidationError) as caught:
            self.settings.full_clean()
        self.assertIn("transfer_payment_details", caught.exception.message_dict)

        self.settings.transfer_payment_details = "Карта"
        self.settings.delivery_fee = Decimal("-1.00")
        with self.assertRaises(ValidationError):
            self.settings.full_clean()

    def test_fee_is_waived_from_the_free_threshold(self):
        self.assertEqual(delivery_fee_for(self.settings, Decimal("999.99")), Decimal("60.00"))
        self.assertEqual(delivery_fee_for(self.settings, Decimal("1000.00")), Decimal("0.00"))
        self.settings.delivery_free_from = None
        self.assertEqual(delivery_fee_for(self.settings, Decimal("5000.00")), Decimal("60.00"))


class DeliveryCartTests(DeliveryFixtureMixin, TestCase):
    def test_cart_without_session_works_when_only_delivery_is_on(self):
        cart = get_or_create_active_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG
        )
        self.assertEqual(cart.channel, D)

    def test_switching_to_delivery_removes_items_not_delivered(self):
        self.settings.module_pickup_enabled = True
        self.settings.save()
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, channel=Order.Channel.PICKUP
        )
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, raw_items="DLV00002x1"
        )
        cart, removed = switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, channel=D
        )
        self.assertEqual(cart.channel, D)
        self.assertEqual(removed, ["Кальян"])

    def test_delivery_off_is_refused(self):
        self.settings.module_delivery_enabled = False
        self.settings.module_pickup_enabled = True
        self.settings.save()
        with self.assertRaisesMessage(OrderFlowError, "Доставка"):
            switch_cart_channel_for_telegram_user(
                partner_id=self.partner.id, telegram_id=TG, channel=D
            )


class DeliveryAddressTests(DeliveryFixtureMixin, TestCase):
    def test_address_is_remembered_and_a_new_text_drops_the_old_point(self):
        save_guest_delivery_location(
            partner_id=self.partner.id, telegram_id=TG, latitude=50.45, longitude=30.52
        )
        guest = save_guest_delivery_address(
            partner_id=self.partner.id, telegram_id=TG, address="  ул. Банковая 2  "
        )
        self.assertEqual(guest.delivery_address, "ул. Банковая 2")
        self.assertIsNone(guest.delivery_latitude)

        guest = save_guest_delivery_location(
            partner_id=self.partner.id, telegram_id=TG, latitude=50.4501, longitude=30.5234
        )
        self.assertEqual(guest.delivery_address, "ул. Банковая 2")
        self.assertEqual(guest.delivery_latitude, Decimal("50.450100"))

    def test_empty_or_bad_input_is_refused(self):
        with self.assertRaises(RemoteOrderError):
            save_guest_delivery_address(partner_id=self.partner.id, telegram_id=TG, address="  ")
        with self.assertRaises(RemoteOrderError):
            save_guest_delivery_location(
                partner_id=self.partner.id, telegram_id=TG, latitude=95, longitude=30
            )


class DeliveryQuoteTests(DeliveryFixtureMixin, TestCase):
    def test_quote_adds_the_fee(self):
        self._cart(2)
        quote = get_remote_quote(
            partner_id=self.partner.id, telegram_id=TG, use_bonuses=False, channel=D
        )
        self.assertEqual(
            (quote.items_total, quote.delivery_fee, quote.total, quote.to_pay),
            (Decimal("400.00"), Decimal("60.00"), Decimal("460.00"), Decimal("460.00")),
        )

    def test_quote_is_free_from_threshold(self):
        self._cart(5)
        quote = get_remote_quote(
            partner_id=self.partner.id, telegram_id=TG, use_bonuses=False, channel=D
        )
        self.assertEqual((quote.delivery_fee, quote.total), (Decimal("0.00"), Decimal("1000.00")))

    def test_minimum_order_is_enforced(self):
        self.settings.delivery_min_order = Decimal("500.00")
        self.settings.save()
        self._cart(2)
        with self.assertRaisesMessage(RemoteOrderError, "500"):
            get_remote_quote(
                partner_id=self.partner.id, telegram_id=TG, use_bonuses=False, channel=D
            )


class PlaceDeliveryOrderTests(DeliveryFixtureMixin, TestCase):
    def test_order_carries_address_and_a_fee_line(self):
        self._cart(2)
        self._ready_guest()
        save_guest_delivery_location(
            partner_id=self.partner.id, telegram_id=TG, latitude=50.45, longitude=30.52
        )
        order = self._place()

        self.assertEqual(order.channel, D)
        self.assertEqual(order.delivery_address, "ул. Крещатик 1, кв. 5")
        self.assertEqual(order.delivery_latitude, Decimal("50.450000"))
        self.assertEqual(order.total_amount, Decimal("460.00"))
        fee = order.items.get(kind=OrderItem.Kind.DELIVERY_FEE)
        self.assertIsNone(fee.menu_item_id)
        self.assertEqual((fee.item_name, fee.unit_price), ("Доставка", Decimal("60.00")))

        bill = Bill.objects.get(bill_orders__order=order)
        self.assertEqual(bill.total_amount, Decimal("460.00"))
        self.assertEqual(Cart.objects.get(guest=self.guest).status, Cart.Status.CHECKED_OUT)
        note = StaffNotification.objects.get(order=order)
        self.assertIn("ул. Крещатик 1", note.message)
        self.assertIn("+380671234567", note.message)

    def test_free_delivery_has_no_fee_line(self):
        self._cart(5)
        self._ready_guest()
        order = self._place(total="1000.00", to_pay="1000.00")
        self.assertFalse(order.items.filter(kind=OrderItem.Kind.DELIVERY_FEE).exists())

    def test_address_is_required(self):
        self._cart(2)
        save_guest_phone(partner_id=self.partner.id, telegram_id=TG, phone="+380671234567")
        with self.assertRaisesMessage(RemoteOrderError, "адрес"):
            self._place()
        self.assertFalse(Order.objects.exists())

    def test_changed_fee_is_caught(self):
        self._cart(2)
        self._ready_guest()
        self.settings.delivery_fee = Decimal("80.00")
        self.settings.save()
        with self.assertRaisesMessage(RemoteOrderError, "изменилась"):
            self._place()
        self.assertFalse(Order.objects.exists())

    def test_bonuses_can_pay_part_including_delivery(self):
        BonusTransaction.objects.create(
            partner=self.partner,
            guest=self.guest,
            transaction_type=BonusTransaction.TransactionType.MANUAL,
            amount="1000.00",
        )
        GuestProfile.objects.filter(id=self.guest.id).update(loyalty_balance="1000.00")
        BonusProgram.objects.create(
            partner=self.partner,
            name="Cashback",
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00",
            max_redeem_share="20.00",
        )
        self._cart(2)
        self._ready_guest()
        quote = get_remote_quote(
            partner_id=self.partner.id, telegram_id=TG, use_bonuses=True, channel=D
        )
        order = self._place(use_bonuses=True, to_pay=str(quote.to_pay))
        # 20 % of 460 (items and delivery) may be paid with bonuses.
        self.assertEqual(quote.bonus_to_spend, Decimal("92.00"))
        bill = Bill.objects.get(bill_orders__order=order)
        self.assertEqual(bill.bonus_spent_amount, Decimal("92.00"))
        self.assertEqual(bill.total_amount, Decimal("368.00"))

    def test_staff_confirm_then_on_the_way_then_delivered(self):
        self._cart(2)
        self._ready_guest()
        order = confirm_transfer(order=self._place(), actor=self.cashier)
        self.assertEqual(order.status, Order.Status.ACCEPTED)

        order = transition_order_status(order=order, to_status=Order.Status.READY)
        self.assertEqual(
            get_available_staff_actions(order.status, channel=D),
            [Order.Status.DELIVERING, Order.Status.CANCELED],
        )
        order = transition_order_status(order=order, to_status=Order.Status.DELIVERING)
        self.assertEqual(
            get_available_staff_actions(order.status, channel=D),
            [Order.Status.COMPLETED, Order.Status.CANCELED],
        )
        # Other channels keep their actions.
        self.assertEqual(
            get_available_staff_actions(Order.Status.READY),
            [Order.Status.COMPLETED, Order.Status.CANCELED],
        )

    def test_guest_hears_about_each_delivery_step(self):
        order = Order(channel=D)
        self.assertIn("курьер", _remote_status_note(order, Order.Status.READY))
        self.assertIn("в пути", _remote_status_note(order, Order.Status.DELIVERING))
        self.assertIn("доставлен", _remote_status_note(order, Order.Status.COMPLETED))


class OrderItemKindTests(DeliveryFixtureMixin, TestCase):
    def test_ordinary_line_needs_a_menu_item_and_fee_line_has_none(self):
        self._cart(2)
        self._ready_guest()
        order = self._place()
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderItem.objects.create(
                partner=self.partner, order=order, item_name="?", unit_price="1.00"
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderItem.objects.create(
                partner=self.partner,
                order=order,
                menu_item=self.item,
                kind=OrderItem.Kind.DELIVERY_FEE,
                item_name="Доставка",
                unit_price="1.00",
            )
