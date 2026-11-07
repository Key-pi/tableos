"""Choosing a variant and add-ons in the guest's bot (catalog step 2, owner
2026-11-07): the choice lives in the callback, the cart service checks it."""

from asgiref.sync import async_to_sync

from apps.menu.models import MenuItem, MenuItemVariant, Modifier, ModifierGroup
from apps.orders.models import CartItem, Order
from bot.handlers import menu as menu_handlers
from bot.handlers import order as order_handlers
from bot.keyboards.options import MAX_ADD_ONS
from tests.apps.menu.test_options import options_module
from tests.bot.test_guest_flow import GuestFlowFixture, _buttons, _data


class BotChoiceFixture(GuestFlowFixture):
    def setUp(self):
        super().setUp()
        options_module(self.partner)
        self.item = MenuItem.objects.get(public_id="FLOW0001")  # Сырники 220.00
        MenuItemVariant.objects.create(item=self.item, name="3 шт", price="220.00", sort_order=1)
        MenuItemVariant.objects.create(item=self.item, name="5 шт", price="330.00", sort_order=2)
        self.sauce = ModifierGroup.objects.create(
            partner=self.partner, name="Соус", min_choices=1, max_choices=1, sort_order=1
        )
        Modifier.objects.create(group=self.sauce, name="Сметана", sort_order=1)
        Modifier.objects.create(group=self.sauce, name="Варенье", price="20.00", sort_order=2)
        self.extra = ModifierGroup.objects.create(
            partner=self.partner, name="Добавить", min_choices=0, max_choices=2, sort_order=2
        )
        for order, name in enumerate(["Мёд", "Ягоды", "Орехи"]):
            Modifier.objects.create(group=self.extra, name=name, price="15.00", sort_order=order)
        self.sauce.items.add(self.item)
        self.extra.items.add(self.item)
        self._choose(Order.Channel.PICKUP)

    def press(self, data, handler=None):
        callback = self._callback(data)
        handler = handler or (
            menu_handlers.menu_choice_add_callback if data.startswith("ma:")
            else menu_handlers.menu_choice_callback
        )
        async_to_sync(handler)(callback, self.bot)
        return callback

    def shown(self, callback):
        call = callback.message.edit_text.call_args
        return call.args[0], call.kwargs["reply_markup"]

    def button(self, markup, starts):
        return next(b for b in _buttons(markup) if b.text.startswith(starts))


class ChooserTests(BotChoiceFixture):
    def test_the_menu_opens_the_choice_instead_of_adding(self):
        text, markup = self._menu()
        self.assertIn("Сырники — от 220.00 грн", text)
        self.assertIn("mo:FLOW0001", _data(markup))
        self.assertNotIn("menuadd:FLOW0001", _data(markup))

    def test_pick_and_add(self):
        text, markup = self.shown(self.press("mo:FLOW0001"))
        self.assertIn("Сырники, 3 шт (Сметана)", text)  # the first of each to start with
        _text, markup = self.shown(self.press(self.button(markup, "○ 5 шт").callback_data))
        _text, markup = self.shown(self.press(self.button(markup, "○ Варенье").callback_data))
        text, markup = self.shown(self.press(self.button(markup, "▫️ Мёд").callback_data))
        self.assertIn("Сырники, 5 шт (Варенье, Мёд)", text)
        add = self.button(markup, "➕ Добавить")
        self.assertEqual(add.text, "➕ Добавить · 365.00 грн")
        callback = self.press(add.callback_data)
        callback.answer.assert_called_with("Добавлено в корзину.")
        line = CartItem.objects.get()
        self.assertEqual((line.item_name, line.quantity), ("Сырники, 5 шт (Варенье, Мёд)", 1))
        cart_button = self.button(
            callback.message.edit_reply_markup.call_args.kwargs["reply_markup"], "🛒"
        )
        self.assertEqual(cart_button.text, "🛒 Корзина: 1 шт. · 365.00 грн")

    def test_a_required_single_choice_cannot_be_emptied(self):
        _text, markup = self.shown(self.press("mo:FLOW0001"))
        same = self.button(markup, "✅ Сметана").callback_data
        text, _markup = self.shown(self.press(same))
        self.assertIn("(Сметана)", text)

    def test_more_than_the_group_allows_is_refused(self):
        _text, markup = self.shown(self.press("mo:FLOW0001"))
        for name in ("▫️ Мёд", "▫️ Ягоды"):
            _text, markup = self.shown(self.press(self.button(markup, name).callback_data))
        callback = self.press(self.button(markup, "▫️ Орехи").callback_data)
        callback.message.edit_text.assert_not_called()
        self.assertIn("не больше 2", callback.answer.call_args.args[0])

    def test_a_stale_or_made_up_pick_adds_nothing(self):
        callback = self.press("ma:FLOW0001:9:0")
        self.assertTrue(callback.answer.call_args.kwargs.get("show_alert"))
        callback = self.press("ma:FLOW0001:0:0")  # no sauce: the service refuses
        self.assertIn("Соус", callback.answer.call_args.args[0])
        self.assertFalse(CartItem.objects.exists())

    def test_every_button_fits_telegram(self):
        for index in range(MAX_ADD_ONS + 5):
            Modifier.objects.create(
                group=self.extra, name=f"Добавка {index}", sort_order=10 + index
            )
        self.extra.max_choices = 0
        self.extra.save()
        _text, markup = self.shown(self.press("mo:FLOW0001"))
        # 2 sauces and 3 extras before them: the 60th add-on is «Добавка 54».
        self.assertNotIn("▫️ Добавка 55", [button.text for button in _buttons(markup)])
        last = self.button(markup, "▫️ Добавка 54").callback_data
        _text, markup = self.shown(self.press(last))
        self.assertIn("✅ Добавка 54", [button.text for button in _buttons(markup)])
        for data in _data(markup):
            self.assertLessEqual(len(data.encode()), 64, data)

    def test_switched_off_the_menu_adds_as_before(self):
        options_module(self.partner, on=False)
        _text, markup = self._menu()
        self.assertIn("menuadd:FLOW0001", _data(markup))


class CartLineTests(BotChoiceFixture):
    def test_the_cart_changes_a_line_by_its_own_id(self):
        self.press("ma:FLOW0001:0:1")  # 3 шт, Сметана
        self.press("ma:FLOW0001:1:1")  # 5 шт, Сметана
        message = self._message()
        async_to_sync(order_handlers.cart_handler)(message, self.bot)
        markup = message.answer.call_args.kwargs["reply_markup"]
        big = CartItem.objects.get(variant__name="5 шт")
        self.assertIn(f"cart:item:add:{big.id.hex}", _data(markup))
        async_to_sync(order_handlers.cart_item_quantity_callback)(
            self._callback(f"cart:item:add:{big.id.hex}"), self.bot
        )
        self.assertEqual(
            sorted(CartItem.objects.values_list("item_name", "quantity")),
            [("Сырники, 3 шт (Сметана)", 1), ("Сырники, 5 шт (Сметана)", 2)],
        )

    def test_an_old_cart_message_by_the_item_code_still_works(self):
        options_module(self.partner, on=False)
        async_to_sync(menu_handlers.menu_add_callback)(self._callback("menuadd:FLOW0001"), self.bot)
        async_to_sync(order_handlers.cart_item_remove_callback)(
            self._callback("cart:item:remove:FLOW0001"), self.bot
        )
        self.assertFalse(CartItem.objects.exists())
