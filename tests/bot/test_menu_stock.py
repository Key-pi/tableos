"""Stock in the guest's bot (catalog step 3, owner 2026-11-07): «нет в
наличии» and «осталось мало» in the menu, never the number; nothing to add
when nothing is left; a variant without stock cannot be picked."""

from asgiref.sync import async_to_sync

from apps.menu.models import MenuItem, MenuItemVariant
from apps.menu.stock import LOW_STOCK
from apps.orders.models import Order
from bot.handlers import menu as menu_handlers
from bot.keyboards.options import NOOP
from tests.apps.menu.test_options import options_module
from tests.apps.menu.test_stock import stock_module
from tests.bot.test_guest_flow import GuestFlowFixture, _buttons, _data


class BotStockFixture(GuestFlowFixture):
    def setUp(self):
        super().setUp()
        stock_module(self.partner)
        options_module(self.partner)
        self.cheesecakes = MenuItem.objects.get(public_id="FLOW0001")
        self.cheesecakes.track_stock = True
        self.cheesecakes.stock_quantity = 0
        self.cheesecakes.save()
        self._choose(Order.Channel.PICKUP)

    def press(self, data):
        callback = self._callback(data)
        async_to_sync(menu_handlers.menu_choice_callback)(callback, self.bot)
        call = callback.message.edit_text.call_args
        return call.args[0], call.kwargs["reply_markup"]


class MenuStockTests(BotStockFixture):
    def test_nothing_left_nothing_to_add(self):
        text, markup = self._menu()
        self.assertIn("Сырники — 220.00 грн — нет в наличии", text)
        self.assertNotIn("menuadd:FLOW0001", _data(markup))

    def test_little_left_says_so_without_the_number(self):
        self.cheesecakes.stock_quantity = LOW_STOCK
        self.cheesecakes.save()
        text, markup = self._menu()
        self.assertIn("Сырники — 220.00 грн · осталось мало", text)
        self.assertNotIn(f"{LOW_STOCK} шт", text)
        self.assertIn("menuadd:FLOW0001", _data(markup))

    def test_switched_off_as_before(self):
        stock_module(self.partner, on=False)
        text, markup = self._menu()
        self.assertNotIn("нет в наличии", text)
        self.assertIn("menuadd:FLOW0001", _data(markup))


class VariantStockTests(BotStockFixture):
    def setUp(self):
        super().setUp()
        MenuItemVariant.objects.create(
            item=self.cheesecakes, name="3 шт", price="220.00", sort_order=1, stock_quantity=0
        )
        MenuItemVariant.objects.create(
            item=self.cheesecakes, name="5 шт", price="330.00", sort_order=2, stock_quantity=2
        )

    def test_the_variant_without_stock_cannot_be_picked(self):
        text, markup = self._menu()
        self.assertIn("· осталось мало", text)
        self.assertIn("mo:FLOW0001", _data(markup))
        text, markup = self.press("mo:FLOW0001")
        self.assertIn("Сырники, 5 шт", text)  # the first one on sale to start with
        out = next(button for button in _buttons(markup) if "3 шт" in button.text)
        self.assertEqual(out.text, "✖ 3 шт — нет в наличии")
        self.assertEqual(out.callback_data, NOOP)
