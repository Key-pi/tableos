"""Guest and staff bot flows for delivery (docs/architecture/DELIVERY.md, PQ-032)."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Order, OrderItem
from apps.orders.services import (
    add_items_to_cart_for_telegram_user,
    switch_cart_channel_for_telegram_user,
    transition_order_status,
)
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.users.models import GuestProfile
from apps.users.services import get_or_create_guest_profile
from bot.handlers import order as order_handlers
from bot.handlers import remote_checkout, staff
from bot.services.runtime import BotRuntimeConfig, register_runtime_configs

GUEST = 7500001


def _user(telegram_id=GUEST):
    return SimpleNamespace(
        id=telegram_id, username="", first_name="G", last_name="", language_code=""
    )


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


class DeliveryBotFixture(TestCase):
    token = "delivery-bot-token"

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Delivery Bot Venue", slug="delivery-bot-venue", status=Partner.Status.ACTIVE
        )
        self.settings = PartnerBotSettings.objects.create(
            partner=self.partner,
            module_menu_enabled=True,
            module_orders_enabled=True,
            module_cart_enabled=True,
            module_tables_enabled=False,
            module_delivery_enabled=True,
            transfer_payment_details="Карта 5375 0000 0000 0000",
            delivery_fee=Decimal("60.00"),
            delivery_free_from=Decimal("1000.00"),
        )
        bot_instance = BotInstance.objects.create(
            partner=self.partner,
            display_name="Delivery Bot",
            username="delivery_bot_venue_bot",
            token_encrypted="",
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
        category = MenuCategory.objects.create(partner=self.partner, name="Еда")
        MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="DB000001",
            name="Суп",
            price="150.00",
        )
        self.guest = get_or_create_guest_profile(partner_id=self.partner.id, telegram_id=GUEST)
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, channel=Order.Channel.DELIVERY
        )
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, raw_items="DB000001x2"
        )
        GuestProfile.objects.filter(id=self.guest.id).update(phone="+380671234567")
        self.bot = SimpleNamespace(token=self.token)

    def tearDown(self):
        register_runtime_configs([])

    def _callback(self, data):
        return SimpleNamespace(
            data=data,
            from_user=_user(),
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()),
        )

    def _press(self, data, handler=None):
        callback = self._callback(data)
        async_to_sync(handler or remote_checkout.checkout_callback_handler)(callback, self.bot)
        return callback

    def _address_reply(self, text):
        prompt = SimpleNamespace(
            text=f"{remote_checkout.ADDRESS_PROMPT}\nНапишите…",
            from_user=SimpleNamespace(is_bot=True),
        )
        return SimpleNamespace(
            text=text, from_user=_user(), reply_to_message=prompt, answer=AsyncMock()
        )


class GuestDeliveryFlowTests(DeliveryBotFixture):
    def test_delivery_button_shows_the_terms_and_the_menu(self):
        message = SimpleNamespace(from_user=_user(), answer=AsyncMock())
        with patch("bot.handlers.order.send_menu_view", new=AsyncMock()) as menu:
            async_to_sync(order_handlers.delivery_handler)(message, self.bot)
        text = message.answer.call_args_list[0].args[0]
        self.assertIn("Доставка 60.00 грн, бесплатно от 1000.00 грн.", text)
        menu.assert_awaited_once()

    def test_checkout_asks_for_an_address_when_none_is_saved(self):
        callback = self._callback("cart:checkout")
        async_to_sync(order_handlers.cart_checkout_callback)(callback, self.bot)
        text = callback.message.answer.call_args.args[0]
        self.assertTrue(text.startswith(remote_checkout.ADDRESS_PROMPT))
        markup = callback.message.answer.call_args.kwargs["reply_markup"]
        self.assertTrue(markup.force_reply)

    def test_answer_to_the_prompt_saves_the_address_and_asks_the_time(self):
        reply = self._address_reply("ул. Хрещатик 1, кв. 5 <b>")
        self.assertTrue(remote_checkout._is_address_answer(reply))
        async_to_sync(remote_checkout.address_handler)(reply, self.bot)

        self.guest.refresh_from_db()
        self.assertEqual(self.guest.delivery_address, "ул. Хрещатик 1, кв. 5 <b>")
        location_markup = reply.answer.call_args_list[0].kwargs["reply_markup"]
        self.assertTrue(location_markup.keyboard[0][0].request_location)
        time_markup = reply.answer.call_args_list[1].kwargs["reply_markup"]
        self.assertIn("rc:d:t:now", [button.callback_data for button in _buttons(time_markup)])

    def test_ordinary_text_is_not_taken_for_an_address(self):
        plain = SimpleNamespace(text="Привет", reply_to_message=None, from_user=_user())
        self.assertFalse(remote_checkout._is_address_answer(plain))
        other_reply = SimpleNamespace(
            text="Привет",
            reply_to_message=SimpleNamespace(
                text="Что-то другое", from_user=SimpleNamespace(is_bot=True)
            ),
        )
        self.assertFalse(remote_checkout._is_address_answer(other_reply))

    def test_saved_address_is_offered_and_location_is_kept(self):
        GuestProfile.objects.filter(id=self.guest.id).update(delivery_address="ул. Банковая 2")
        location = SimpleNamespace(
            from_user=_user(),
            location=SimpleNamespace(latitude=50.45, longitude=30.52),
            answer=AsyncMock(),
        )
        async_to_sync(remote_checkout.location_handler)(location, self.bot)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.delivery_latitude, Decimal("50.450000"))

        callback = self._callback("cart:checkout")
        async_to_sync(order_handlers.cart_checkout_callback)(callback, self.bot)
        text = callback.message.answer.call_args.args[0]
        self.assertIn("ул. Банковая 2", text)
        self.assertIn("Точка на карте сохранена", text)
        data = [
            b.callback_data
            for b in _buttons(callback.message.answer.call_args.kwargs["reply_markup"])
        ]
        self.assertIn("rc:d:a:ok", data)
        self.assertIn("rc:d:a:new", data)

    def test_summary_lists_the_fee_and_placing_creates_the_delivery_order(self):
        GuestProfile.objects.filter(id=self.guest.id).update(delivery_address="ул. Банковая 2")
        callback = self._press("rc:d:t:now")
        text = callback.message.edit_text.call_args.args[0]
        self.assertIn("Позиции: 300.00 грн", text)
        self.assertIn("Доставка: 60.00 грн", text)
        self.assertIn("К оплате переводом: 360.00 грн", text)
        place = next(
            b.callback_data
            for b in _buttons(callback.message.edit_text.call_args.kwargs["reply_markup"])
            if b.callback_data.startswith("rc:d:p:")
        )
        self.assertLessEqual(len(place.encode()), 64)

        self._press(place)
        order = Order.objects.get()
        self.assertEqual(order.channel, Order.Channel.DELIVERY)
        self.assertEqual(order.delivery_address, "ул. Банковая 2")
        self.assertTrue(order.items.filter(kind=OrderItem.Kind.DELIVERY_FEE).exists())

    def test_location_outside_delivery_is_not_stored(self):
        self.settings.module_pickup_enabled = True
        self.settings.save()
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, channel=Order.Channel.PICKUP
        )
        location = SimpleNamespace(
            from_user=_user(),
            location=SimpleNamespace(latitude=50.45, longitude=30.52),
            answer=AsyncMock(),
        )
        async_to_sync(remote_checkout.location_handler)(location, self.bot)
        self.guest.refresh_from_db()
        self.assertIsNone(self.guest.delivery_latitude)


class StaffDeliveryCardTests(DeliveryBotFixture):
    def _order(self):
        GuestProfile.objects.filter(id=self.guest.id).update(
            delivery_address="ул. Банковая 2", delivery_latitude="50.45", delivery_longitude="30.52"
        )
        self._press("rc:d:p:now:0:36000:36000")
        return Order.objects.get()

    def test_card_shows_address_map_and_phone(self):
        order = self._order()
        card = staff._order_context_label(order)
        self.assertIn("Адрес: ул. Банковая 2", card)
        self.assertIn("https://maps.google.com/?q=50.450000,30.520000", card)
        self.assertIn("+380671234567", card)

    def test_ready_order_offers_on_the_way_then_delivered(self):
        order = self._order()
        Order.objects.filter(id=order.id).update(status=Order.Status.ACCEPTED)
        order.refresh_from_db()
        order = transition_order_status(order=order, to_status=Order.Status.READY)
        buttons = _buttons(staff._order_actions_keyboard(order))
        self.assertIn("🚚 В пути", [b.text for b in buttons])
        order = transition_order_status(order=order, to_status=Order.Status.DELIVERING)
        buttons = _buttons(staff._order_actions_keyboard(order))
        self.assertIn("✅ Доставлено", [b.text for b in buttons])
