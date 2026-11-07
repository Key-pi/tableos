"""Stock of items and variants (catalog step 3, owner 2026-11-07).

Decisions: an order takes its goods at checkout and a canceled order gives
them back; the guest sees «нет в наличии» and «осталось мало», no number; the
quick sale takes too and may go below zero; a refund puts goods back only
when the staff tick it. Module ``module_stock_enabled``; an item is counted
only with «Учитывать остаток».
"""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase

from apps.billing.services import cancel_order
from apps.bonuses.services import register_walk_in_sale_from_menu_items_by_customer_code
from apps.menu.models import MenuItem, MenuItemVariant, StockMovement
from apps.menu.stock import (
    LOW_STOCK,
    StockError,
    count_stock,
    item_state,
    level,
    state_of,
)
from apps.orders.models import CartItem, Order
from apps.orders.services import (
    OrderFlowError,
    add_menu_item_for_telegram_user,
    change_cart_item_quantity_for_telegram_user,
    create_order,
    switch_cart_channel_for_telegram_user,
)
from apps.partners.models import PartnerBotSettings
from apps.users.models import GuestProfile, TelegramAccount, User
from tests.apps.menu.test_options import options_module
from tests.webapp.test_guest_api import make_venue

TG = 7700301


def stock_module(partner, on=True):
    PartnerBotSettings.objects.filter(partner=partner).update(module_stock_enabled=on)


class StockFixture(TestCase):
    def setUp(self):
        self.partner = make_venue("Shop")
        stock_module(self.partner)
        options_module(self.partner)
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")  # not counted
        self.shirt = MenuItem.objects.create(
            partner=self.partner, category=self.latte.category, name="Футболка",
            price="500.00", track_stock=True, available_for_pickup=True,
        )
        self.m = MenuItemVariant.objects.create(
            item=self.shirt, name="M", price="500.00", stock_quantity=2
        )
        self.l = MenuItemVariant.objects.create(
            item=self.shirt, name="L", price="500.00", stock_quantity=0
        )
        self.mouthpiece = MenuItem.objects.create(
            partner=self.partner, category=self.latte.category, name="Мундштук",
            price="100.00", track_stock=True, stock_quantity=1, available_for_pickup=True,
        )
        self.guest = GuestProfile.objects.create(
            partner=self.partner, telegram_account=TelegramAccount.objects.create(telegram_id=TG)
        )

    def order(self, *lines, channel=Order.Channel.PICKUP):
        items = []
        for item, variant, quantity in lines:
            items.append({
                "menu_item_id": item.id, "item_name": item.name, "unit_price": item.price,
                "quantity": quantity, "variant_id": variant.id if variant else None,
            })
        return create_order(
            partner_id=self.partner.id, guest=self.guest, items=items, channel=channel,
            payment_timing=Order.PaymentTiming.ON_RECEIPT, notify_staff=False,
        )

    def stock(self, item, variant=None):
        if variant is not None:
            variant.refresh_from_db()
            return variant.stock_quantity
        item.refresh_from_db()
        return item.stock_quantity


class LevelTests(StockFixture):
    def test_levels_and_what_the_guest_is_told(self):
        self.assertIsNone(level(self.latte))  # not counted
        self.assertEqual(level(self.shirt, self.m), 2)
        self.assertEqual(level(self.mouthpiece), 1)
        self.assertEqual(state_of(0), "out")
        self.assertEqual(state_of(LOW_STOCK), "low")
        self.assertEqual(state_of(LOW_STOCK + 1), "")
        self.assertEqual(state_of(None), "")
        self.assertEqual(item_state(self.shirt, [self.m, self.l]), "low")
        self.assertEqual(item_state(self.shirt, [self.l]), "out")

    def test_switched_off_nothing_is_counted(self):
        stock_module(self.partner, on=False)
        self.mouthpiece.refresh_from_db()
        self.assertIsNone(level(self.mouthpiece))
        self.order((self.mouthpiece, None, 5))  # no limit
        self.assertEqual(self.stock(self.mouthpiece), 1)
        self.assertFalse(StockMovement.objects.exists())


class OrderTests(StockFixture):
    def test_the_order_takes_and_a_cancel_gives_back(self):
        order = self.order(
            (self.shirt, self.m, 2), (self.mouthpiece, None, 1), (self.latte, None, 3)
        )
        self.assertEqual((self.stock(self.shirt, self.m), self.stock(self.mouthpiece)), (0, 0))
        cancel_order(order=order)
        self.assertEqual((self.stock(self.shirt, self.m), self.stock(self.mouthpiece)), (2, 1))
        reasons = list(StockMovement.objects.order_by("created_at").values_list("reason", "delta"))
        self.assertEqual(sorted(reasons), sorted([
            ("order", -2), ("order", -1), ("cancel", 2), ("cancel", 1),
        ]))

    def test_more_than_there_is_cannot_be_ordered(self):
        with self.assertRaisesMessage(OrderFlowError, "Футболка"):
            self.order((self.shirt, self.m, 3))
        with self.assertRaises(OrderFlowError):
            self.order((self.shirt, self.l, 1))
        self.assertEqual(self.stock(self.shirt, self.m), 2)
        self.assertFalse(Order.objects.exists())  # the whole checkout rolled back

    def test_the_cart_does_not_take_more_than_there_is(self):
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, channel="pickup"
        )
        add = dict(
            partner_id=self.partner.id, telegram_id=TG, item_public_id=self.mouthpiece.public_id
        )
        add_menu_item_for_telegram_user(**add)
        with self.assertRaisesMessage(OrderFlowError, "Мундштук"):
            add_menu_item_for_telegram_user(**add)
        line = CartItem.objects.get()
        with self.assertRaises(OrderFlowError):
            change_cart_item_quantity_for_telegram_user(
                partner_id=self.partner.id, telegram_id=TG, line_id=line.id.hex, delta=1
            )
        with self.assertRaises(OrderFlowError):
            add_menu_item_for_telegram_user(
                partner_id=self.partner.id, telegram_id=TG,
                item_public_id=self.shirt.public_id, variant_id=self.l.id.hex,
            )

    def test_canceling_twice_gives_back_once(self):
        order = self.order((self.mouthpiece, None, 1))
        cancel_order(order=order)
        with self.assertRaises(OrderFlowError):
            cancel_order(order=order)
        self.assertEqual(self.stock(self.mouthpiece), 1)


class ModuleIndependenceTests(StockFixture):
    """«Остатки» works alone and leaves the other modules as they were
    (PQ-033)."""

    def test_alone_without_variants_the_item_counts(self):
        options_module(self.partner, on=False)
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_showcase_enabled=False
        )
        self.order((self.mouthpiece, None, 1), (self.latte, None, 2))
        self.assertEqual(self.stock(self.mouthpiece), 0)
        with self.assertRaises(OrderFlowError):
            self.order((self.mouthpiece, None, 1))
        self.order((self.latte, None, 9))  # not counted: as before

    def test_switched_off_variants_are_chosen_as_before(self):
        stock_module(self.partner, on=False)
        order = self.order((self.shirt, self.l, 3))  # L has none: not counted now
        self.assertEqual(order.items.get().variant, self.l)
        self.assertEqual(self.stock(self.shirt, self.l), 0)


class QuickSaleTests(StockFixture):
    def test_the_till_takes_and_may_go_below_zero(self):
        register_walk_in_sale_from_menu_items_by_customer_code(
            partner_id=self.partner.id, customer_code="",
            items=[{"menu_item_id": self.mouthpiece.id, "quantity": 3}],
        )
        self.assertEqual(self.stock(self.mouthpiece), -2)
        movement = StockMovement.objects.get()
        self.assertEqual((movement.reason, movement.delta, movement.balance_after),
                         ("quick_sale", -3, -2))


class CountTests(StockFixture):
    def test_a_count_sets_the_level_and_is_recorded(self):
        owner = User.objects.create_user(username="o", password="x", partner=self.partner)
        count_stock(item=self.shirt, variant=self.l, quantity=7, actor=owner)
        self.assertEqual(self.stock(self.shirt, self.l), 7)
        movement = StockMovement.objects.get()
        self.assertEqual((movement.reason, movement.delta, movement.actor), ("count", 7, owner))
        with self.assertRaises(StockError):
            count_stock(item=self.latte, variant=None, quantity=3, actor=owner)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency test")
class LastItemRaceTests(TransactionTestCase):
    """Two guests check out the last mouthpiece at once: one order."""

    def setUp(self):
        self.partner = make_venue("Race")
        stock_module(self.partner)
        self.item = MenuItem.objects.create(
            partner=self.partner, category=MenuItem.objects.filter(partner=self.partner)
            .first().category, name="Мундштук", price="100.00", track_stock=True,
            stock_quantity=1, available_for_pickup=True,
        )
        self.guests = [
            GuestProfile.objects.create(
                partner=self.partner,
                telegram_account=TelegramAccount.objects.create(telegram_id=TG + n),
            )
            for n in (10, 11)
        ]

    def _buy(self, guest, barrier):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return create_order(
                partner_id=self.partner.id, guest=guest, channel=Order.Channel.PICKUP,
                payment_timing=Order.PaymentTiming.ON_RECEIPT, notify_staff=False,
                items=[{"menu_item_id": self.item.id, "item_name": "Мундштук",
                        "unit_price": Decimal("100.00"), "quantity": 1}],
            )
        except OrderFlowError:
            return None
        finally:
            close_old_connections()

    def test_only_one_gets_it(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda guest: self._buy(guest, barrier), self.guests))
        self.assertEqual(sum(1 for order in results if order is not None), 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.stock_quantity, 0)
