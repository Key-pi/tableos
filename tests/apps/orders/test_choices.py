"""Variants and add-ons in the cart, the order and the quick sale (catalog
step 2, owner 2026-11-07): the same item with another choice is another
line; the line's name and price include the choice, so bills, the kitchen
screen and refunds show it as it is."""

from decimal import Decimal

from django.test import TestCase

from apps.bonuses.services import (
    BonusServiceError,
    register_walk_in_sale_from_menu_items_by_customer_code,
)
from apps.menu.models import MenuItem, MenuItemVariant, Modifier, ModifierGroup
from apps.orders.models import CartItem, OrderItem
from apps.orders.services import (
    OrderFlowError,
    add_items_to_cart_for_telegram_user,
    add_menu_item_for_telegram_user,
    change_cart_item_quantity_for_telegram_user,
    checkout_active_cart_for_telegram_user,
    remove_cart_item_for_telegram_user,
    switch_cart_channel_for_telegram_user,
)
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import GuestProfile, TelegramAccount
from tests.apps.menu.test_options import options_module
from tests.webapp.test_guest_api import make_venue

TG = 7700202


class ChoicesFixture(TestCase):
    def setUp(self):
        self.partner = make_venue("Shop")
        options_module(self.partner)
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")  # 80.00
        self.small = MenuItemVariant.objects.create(
            item=self.latte, name="250 мл", price="80.00", sort_order=1
        )
        self.big = MenuItemVariant.objects.create(
            item=self.latte, name="400 мл", price="95.00", sort_order=2
        )
        milk = ModifierGroup.objects.create(
            partner=self.partner, name="Молоко", min_choices=1, max_choices=1, sort_order=1
        )
        self.cow = Modifier.objects.create(group=milk, name="Коров'яче", sort_order=1)
        self.oat = Modifier.objects.create(group=milk, name="Вівсяне", price="15.00", sort_order=2)
        milk.items.add(self.latte)
        self.milk = milk
        self.guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=TelegramAccount.objects.create(telegram_id=TG),
        )

    def who(self):
        return {"partner_id": self.partner.id, "telegram_id": TG}

    def add(self, variant=None, modifiers=(), item=None):
        return add_menu_item_for_telegram_user(
            **self.who(), item_public_id=(item or self.latte).public_id,
            variant_id=variant.id.hex if variant else None,
            modifier_ids=[modifier.id.hex for modifier in modifiers],
        )

    def lines(self):
        return {
            line.item_name: (line.quantity, line.unit_price)
            for line in CartItem.objects.filter(cart__guest=self.guest)
        }


class CartTests(ChoicesFixture):
    def setUp(self):
        super().setUp()
        switch_cart_channel_for_telegram_user(**self.who(), channel="pickup")

    def test_another_choice_is_another_line_the_same_one_adds_up(self):
        self.add(self.big, [self.oat])
        self.add(self.big, [self.oat])
        cart = self.add(self.small, [self.cow])
        self.assertEqual(self.lines(), {
            "Лате, 400 мл (Вівсяне)": (2, Decimal("110.00")),
            "Лате, 250 мл (Коров'яче)": (1, Decimal("80.00")),
        })
        self.assertEqual(cart.total_amount, Decimal("300.00"))
        line = CartItem.objects.get(item_name="Лате, 400 мл (Вівсяне)")
        self.assertEqual(line.variant, self.big)
        self.assertEqual([row["name"] for row in line.modifiers], ["Вівсяне"])

    def test_without_the_choice_it_does_not_go_in(self):
        with self.assertRaisesMessage(OrderFlowError, "Выберите вариант"):
            self.add()
        with self.assertRaisesMessage(OrderFlowError, "Молоко"):
            self.add(self.big)
        with self.assertRaises(OrderFlowError):  # by codes there is nowhere to choose
            add_items_to_cart_for_telegram_user(
                **self.who(), raw_items=f"{self.latte.public_id}x1"
            )
        self.assertEqual(self.lines(), {})

    def test_a_line_changes_and_goes_by_its_own_id(self):
        self.add(self.big, [self.oat])
        self.add(self.small, [self.cow])
        big = CartItem.objects.get(variant=self.big)
        change_cart_item_quantity_for_telegram_user(**self.who(), line_id=big.id.hex, delta=2)
        self.assertEqual(self.lines()["Лате, 400 мл (Вівсяне)"][0], 3)
        cart = remove_cart_item_for_telegram_user(**self.who(), line_id=big.id.hex)
        self.assertEqual(list(self.lines()), ["Лате, 250 мл (Коров'яче)"])
        self.assertEqual(cart.total_amount, Decimal("80.00"))

    def test_a_line_of_another_guest_is_not_found(self):
        self.add(self.big, [self.oat])
        line = CartItem.objects.get()
        other = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=TelegramAccount.objects.create(telegram_id=TG + 1),
        )
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=other.telegram_account.telegram_id,
            channel="pickup",
        )
        with self.assertRaises(OrderFlowError):
            remove_cart_item_for_telegram_user(
                partner_id=self.partner.id, telegram_id=TG + 1, line_id=line.id.hex
            )
        self.assertTrue(CartItem.objects.filter(id=line.id).exists())

    def test_plain_items_by_their_code_as_before(self):
        hookah_free = MenuItem.objects.create(
            partner=self.partner, category=self.latte.category, name="Чай", price="40.00"
        )
        add_items_to_cart_for_telegram_user(
            **self.who(), raw_items=f"{hookah_free.public_id}x2"
        )
        change_cart_item_quantity_for_telegram_user(
            **self.who(), item_public_id=hookah_free.public_id, delta=1
        )
        self.assertEqual(self.lines(), {"Чай": (3, Decimal("40.00"))})


class TableCheckoutTests(ChoicesFixture):
    def setUp(self):
        super().setUp()
        table = Table.objects.create(partner=self.partner, number=3)
        activate_table_session(**self.who(), payload=table.deep_link_payload)

    def test_the_order_keeps_what_was_chosen(self):
        self.add(self.big, [self.oat])
        order = checkout_active_cart_for_telegram_user(**self.who())
        line = OrderItem.objects.get(order=order)
        self.assertEqual(
            (line.item_name, line.unit_price, line.variant, line.modifiers[0]["name"]),
            ("Лате, 400 мл (Вівсяне)", Decimal("110.00"), self.big, "Вівсяне"),
        )
        self.assertEqual(order.total_amount, Decimal("110.00"))

    def test_a_choice_no_longer_on_sale_is_not_ordered(self):
        self.add(self.big, [self.oat])
        self.oat.is_available = False
        self.oat.save()
        with self.assertRaisesMessage(OrderFlowError, "Лате, 400 мл (Вівсяне)"):
            checkout_active_cart_for_telegram_user(**self.who())

    def test_switching_the_module_off_stops_an_old_choice_too(self):
        self.add(self.big, [self.oat])
        options_module(self.partner, on=False)
        with self.assertRaises(OrderFlowError):
            checkout_active_cart_for_telegram_user(**self.who())


class QuickSaleTests(ChoicesFixture):
    def sell(self, **line):
        return register_walk_in_sale_from_menu_items_by_customer_code(
            partner_id=self.partner.id, customer_code="", items=[
                {"menu_item_id": self.latte.id, "quantity": 2, **line},
            ],
        )

    def test_the_sale_is_at_the_price_of_the_choice(self):
        sale = self.sell(variant_id=self.big.id.hex, modifier_ids=[self.oat.id.hex])
        self.assertEqual(sale.amount, Decimal("220.00"))
        line = sale.items.get()
        self.assertEqual((line.item_name, line.variant), ("Лате, 400 мл (Вівсяне)", self.big))

    def test_without_the_choice_there_is_no_sale(self):
        with self.assertRaises(BonusServiceError):
            self.sell()
