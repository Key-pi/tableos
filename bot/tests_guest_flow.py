"""The guest's way through the bot (2026-10-28): choose the way of ordering
first, the menu only shows until then, texts say what to do next."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Cart, Order
from apps.orders.services import (
    get_active_cart_for_telegram_user,
    switch_cart_channel_for_telegram_user,
)
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.services import get_or_create_guest_profile
from bot.handlers import menu as menu_handlers
from bot.handlers import order as order_handlers
from bot.handlers import remote_checkout, start
from bot.services.content import BotContent
from bot.services.navigation import GuestAction, resolve_guest_navigation_state
from bot.services.runtime import BotRuntimeConfig, register_runtime_configs

GUEST = 7720001


def _user():
    return SimpleNamespace(
        id=GUEST, username="", first_name="G", last_name="", language_code=""
    )


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _data(markup):
    return [button.callback_data for button in _buttons(markup)]


class GuestFlowFixture(TestCase):
    token = "guest-flow-token"

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Flow Cafe", slug="flow-cafe", status=Partner.Status.ACTIVE
        )
        self.settings = PartnerBotSettings.objects.create(
            partner=self.partner,
            module_menu_enabled=True,
            module_orders_enabled=True,
            module_cart_enabled=True,
            module_tables_enabled=True,
            module_pickup_enabled=True,
            module_delivery_enabled=True,
            module_shipping_enabled=True,
            module_staff_call_enabled=True,
            module_billing_enabled=True,
            transfer_payment_details="Карта 5375 0000 0000 0000",
        )
        bot_instance = BotInstance.objects.create(
            partner=self.partner, display_name="Flow", username="flow_cafe_bot", token_encrypted=""
        )
        register_runtime_configs(
            [
                BotRuntimeConfig(
                    partner_id=str(self.partner.id),
                    bot_instance_id=str(bot_instance.id),
                    username=bot_instance.username,
                    token=self.token,
                )
            ]
        )
        breakfast = MenuCategory.objects.create(
            partner=self.partner, name="Завтраки", sort_order=1
        )
        beans = MenuCategory.objects.create(
            partner=self.partner, name="Кофе в зёрнах", sort_order=2
        )
        MenuItem.objects.create(
            partner=self.partner,
            category=breakfast,
            public_id="FLOW0001",
            name="Сырники",
            price="220.00",
            available_for_shipping=False,
        )
        MenuItem.objects.create(
            partner=self.partner, category=beans, public_id="FLOW0002", name="Эфиопия", price="320"
        )
        self.guest = get_or_create_guest_profile(partner_id=self.partner.id, telegram_id=GUEST)
        self.bot = SimpleNamespace(token=self.token)

    def tearDown(self):
        register_runtime_configs([])

    def _message(self, text=""):
        return SimpleNamespace(text=text, from_user=_user(), answer=AsyncMock())

    def _callback(self, data, markup=None):
        return SimpleNamespace(
            data=data,
            from_user=_user(),
            answer=AsyncMock(),
            message=SimpleNamespace(
                answer=AsyncMock(),
                edit_text=AsyncMock(),
                edit_reply_markup=AsyncMock(),
                reply_markup=markup,
            ),
        )

    def _menu(self):
        message = self._message()
        async_to_sync(menu_handlers.send_menu_view)(target=message, bot=self.bot)
        return message.answer.call_args.args[0], message.answer.call_args.kwargs["reply_markup"]

    def _choose(self, channel):
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, channel=channel
        )

    def _sit_down(self):
        table = Table.objects.create(partner=self.partner, number=5, qr_token="flow-5")
        return activate_table_session(
            partner_id=self.partner.id, telegram_id=GUEST, payload=table.deep_link_payload
        )


class MenuBeforeChoosingTests(GuestFlowFixture):
    def test_menu_is_only_for_looking_and_offers_the_ways(self):
        text, markup = self._menu()
        data = _data(markup)
        self.assertFalse([d for d in data if d.startswith("menuadd:")])
        self.assertEqual(
            [d for d in data if d.startswith("pick:")],
            ["pick:pickup", "pick:delivery", "pick:shipping"],
        )
        self.assertIn("выберите ниже, как получите заказ", text)
        self.assertIn("За столом — отсканируйте QR-код на столе.", text)

    def test_adding_before_choosing_asks_to_choose(self):
        callback = self._callback("menuadd:FLOW0001")
        async_to_sync(menu_handlers.menu_add_callback)(callback, self.bot)
        self.assertIn("Сначала выберите", callback.answer.call_args.args[0])
        self.assertFalse(Cart.objects.exists())

    def test_choosing_by_button_opens_that_menu_as_a_new_message(self):
        callback = self._callback("pick:delivery")
        async_to_sync(order_handlers.pick_way_callback)(callback, self.bot)

        cart = get_active_cart_for_telegram_user(partner_id=self.partner.id, telegram_id=GUEST)
        self.assertEqual(cart.channel, Order.Channel.DELIVERY)
        callback.message.edit_text.assert_not_awaited()
        menu_text = callback.message.answer.call_args.args[0]
        self.assertIn("<b>Доставка.</b>", menu_text)
        menu_markup = callback.message.answer.call_args.kwargs["reply_markup"]
        self.assertIn("menuadd:FLOW0001", _data(menu_markup))

    def test_unknown_way_is_ignored(self):
        callback = self._callback("pick:table")
        async_to_sync(order_handlers.pick_way_callback)(callback, self.bot)
        self.assertFalse(Cart.objects.exists())
        callback.message.answer.assert_not_awaited()


class MenuAfterChoosingTests(GuestFlowFixture):
    def test_remote_menu_never_asks_for_the_table_qr(self):
        for channel in (Order.Channel.PICKUP, Order.Channel.DELIVERY, Order.Channel.SHIPPING):
            self._choose(channel)
            text, markup = self._menu()
            self.assertNotIn("QR", text)
            self.assertTrue([d for d in _data(markup) if d.startswith("menuadd:")])

    def test_categories_without_items_for_the_way_are_hidden(self):
        self._choose(Order.Channel.SHIPPING)
        text, markup = self._menu()
        labels = [button.text for button in _buttons(markup)]
        self.assertNotIn("Завтраки", " ".join(labels))
        self.assertIn("Кофе в зёрнах", text)
        self.assertIn("menuadd:FLOW0002", _data(markup))

    def test_adding_updates_the_cart_button(self):
        self._choose(Order.Channel.PICKUP)
        _text, markup = self._menu()
        callback = self._callback("menuadd:FLOW0001", markup=markup)
        async_to_sync(menu_handlers.menu_add_callback)(callback, self.bot)
        new_markup = callback.message.edit_reply_markup.call_args.kwargs["reply_markup"]
        cart_button = next(b for b in _buttons(new_markup) if b.callback_data == "cart:refresh")
        self.assertEqual(cart_button.text, "🛒 Корзина: 1 шт. · 220.00 грн")

    def test_shipping_cart_says_shipping_and_leads_back_to_the_menu(self):
        self._choose(Order.Channel.SHIPPING)
        async_to_sync(menu_handlers.menu_add_callback)(self._callback("menuadd:FLOW0002"), self.bot)
        message = self._message()
        async_to_sync(order_handlers.cart_handler)(message, self.bot)
        self.assertIn("• отправка почтой", message.answer.call_args.args[0])
        self.assertIn("menurefresh", _data(message.answer.call_args.kwargs["reply_markup"]))


class TableTests(GuestFlowFixture):
    def test_at_a_table_the_remote_ways_are_not_on_the_keyboard(self):
        self._sit_down()
        state = resolve_guest_navigation_state(
            partner_id=self.partner.id,
            telegram_id=GUEST,
            content=BotContent.for_partner(self.partner),
        )
        for action in (GuestAction.PICKUP, GuestAction.DELIVERY, GuestAction.SHIPPING):
            self.assertFalse(state.has_action(action))
        self.assertTrue(state.has_action(GuestAction.MENU))

    def test_take_away_and_back_to_the_table(self):
        session = self._sit_down()
        async_to_sync(menu_handlers.menu_add_callback)(self._callback("menuadd:FLOW0001"), self.bot)

        callback = self._callback("cart:mode:pickup")
        async_to_sync(order_handlers.cart_mode_callback)(callback, self.bot)
        markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
        self.assertIn("cart:mode:table", _data(markup))

        callback = self._callback("cart:mode:table")
        async_to_sync(order_handlers.cart_mode_callback)(callback, self.bot)
        cart = get_active_cart_for_telegram_user(partner_id=self.partner.id, telegram_id=GUEST)
        self.assertEqual(cart.channel, Order.Channel.TABLE)
        self.assertEqual(cart.table_session_id, session.id)
        self.assertEqual(cart.items.get().quantity, 1)

    def test_table_menu_has_no_list_of_actions(self):
        self._sit_down()
        text, _markup = self._menu()
        self.assertNotIn("Доступные действия", text)
        self.assertIn("Активный стол: #5", text)


class StartAndHelpTests(GuestFlowFixture):
    def test_start_greets_briefly_and_offers_the_ways(self):
        message = self._message("/start")
        async_to_sync(start.start_handler)(message, SimpleNamespace(args=""), self.bot)
        greeting, choice = (call.args[0] for call in message.answer.call_args_list)
        self.assertNotIn("QR", greeting)
        self.assertIn("Ваш код для бонусов", greeting)
        self.assertIn("Как хотите заказать?", choice)
        self.assertIn("За столом — отсканируйте QR-код на столе.", choice)
        self.assertEqual(
            _data(message.answer.call_args_list[1].kwargs["reply_markup"]),
            ["pick:pickup", "pick:delivery", "pick:shipping"],
        )

    def test_help_has_one_line_per_way_and_does_not_contradict_itself(self):
        text = BotContent.for_partner(self.partner).ordering_help_message()
        self.assertNotIn("Активируйте стол", text)
        self.assertEqual(text.count("• "), 4)

    def test_help_without_tables_does_not_mention_the_qr(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(module_tables_enabled=False)
        self.partner.refresh_from_db()
        text = BotContent.for_partner(self.partner).ordering_help_message()
        self.assertNotIn("QR", text)
        self.assertEqual(text.count("• "), 3)


class InnerDetailsTests(GuestFlowFixture):
    def test_the_guest_does_not_see_notification_counts(self):
        from bot.handlers import guest_call

        self._sit_down()
        callback = self._callback("guestcall:request:waiter")
        with patch.object(guest_call, "request_staff_assistance", return_value=(5, 3)):
            async_to_sync(guest_call.guest_call_request_callback)(callback, self.bot)
        text = callback.message.edit_text.call_args.args[0]
        self.assertNotIn("очеред", text)

    def test_skipping_the_location_brings_the_main_keyboard_back(self):
        message = self._message("Без геолокации")
        self.assertTrue(remote_checkout._is_skip_location(message))
        with patch.object(remote_checkout, "_main_keyboard", new=AsyncMock(return_value="kb")):
            async_to_sync(remote_checkout.skip_location_handler)(message, self.bot)
        self.assertEqual(message.answer.call_args.kwargs["reply_markup"], "kb")


class OrderCardTests(GuestFlowFixture):
    def test_card_lists_what_was_ordered_and_how_to_pay(self):
        from apps.orders.selectors import get_orders_for_session
        from bot.handlers.session import _build_guest_order_card_text

        session = self._sit_down()
        async_to_sync(menu_handlers.menu_add_callback)(self._callback("menuadd:FLOW0001"), self.bot)
        checkout = self._callback("cart:checkout")
        async_to_sync(order_handlers.cart_checkout_callback)(checkout, self.bot)
        order = get_orders_for_session(session.id, partner_id=self.partner.id).get()

        content = BotContent.for_partner(self.partner)
        text = _build_guest_order_card_text(order, content)
        self.assertIn("Сырники × 1", text)
        self.assertIn("Не оплачен", text)
        self.assertIn(f"«{content.button_request_bill_label}»", text)
        self.assertNotIn("ещё не назначен", text)


class LeftTheTableTests(GuestFlowFixture):
    def test_checkout_after_leaving_the_table_asks_for_a_way(self):
        from apps.tables.services import close_table_session

        session = self._sit_down()
        async_to_sync(menu_handlers.menu_add_callback)(self._callback("menuadd:FLOW0001"), self.bot)
        close_table_session(session)

        message = self._message("Оформить заказ")
        async_to_sync(order_handlers.checkout_handler)(message, self.bot)
        self.assertIn("Сначала выберите", message.answer.call_args.args[0])
        self.assertIn("pick:pickup", _data(message.answer.call_args.kwargs["reply_markup"]))
        self.assertFalse(Order.objects.exists())

        cart_message = self._message()
        async_to_sync(order_handlers.cart_handler)(cart_message, self.bot)
        self.assertIn("способ не выбран", cart_message.answer.call_args.args[0])
        cart_data = _data(cart_message.answer.call_args.kwargs["reply_markup"])
        self.assertNotIn("cart:checkout", cart_data)
        self.assertIn("pick:delivery", cart_data)
