"""Guest and staff bot flows for shipping by post (SHIPPING.md, PQ-034)."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Order
from apps.orders.services import (
    add_items_to_cart_for_telegram_user,
    switch_cart_channel_for_telegram_user,
    transition_order_status,
)
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.users.models import GuestProfile, TelegramAccount, User
from apps.users.services import get_or_create_guest_profile
from bot.handlers import order as order_handlers
from bot.handlers import remote_checkout, session, staff
from bot.services.runtime import BotRuntimeConfig, register_runtime_configs

GUEST = 7700001
STAFF = 7700901


def _user(telegram_id=GUEST):
    return SimpleNamespace(
        id=telegram_id, username="", first_name="G", last_name="", language_code=""
    )


def _data(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


class ShippingBotFixture(TestCase):
    token = "shipping-bot-token"

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Shipping Bot Venue", slug="shipping-bot-venue", status=Partner.Status.ACTIVE
        )
        self.settings = PartnerBotSettings.objects.create(
            partner=self.partner,
            module_menu_enabled=True,
            module_orders_enabled=True,
            module_cart_enabled=True,
            module_tables_enabled=False,
            module_shipping_enabled=True,
            transfer_payment_details="Карта 5375 0000 0000 0000",
            shipping_nova_poshta=True,
            shipping_ukrposhta=True,
            shipping_cost_carrier=True,
            shipping_cost_fixed=True,
            shipping_fee=Decimal("70.00"),
            shipping_pay_on_receipt=True,
        )
        bot_instance = BotInstance.objects.create(
            partner=self.partner,
            display_name="Shipping Bot",
            username="shipping_bot_venue_bot",
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
        category = MenuCategory.objects.create(partner=self.partner, name="Кофе")
        MenuItem.objects.create(
            partner=self.partner, category=category, public_id="SB000001", name="Зерно",
            price="300.00",
        )
        self.guest = get_or_create_guest_profile(partner_id=self.partner.id, telegram_id=GUEST)
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, channel=Order.Channel.SHIPPING
        )
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, raw_items="SB000001x2"
        )
        GuestProfile.objects.filter(id=self.guest.id).update(phone="+380671234567")
        self.bot = SimpleNamespace(token=self.token)

    def tearDown(self):
        register_runtime_configs([])

    def _callback(self, data, telegram_id=GUEST):
        return SimpleNamespace(
            data=data,
            from_user=_user(telegram_id),
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()),
        )

    def _press(self, data):
        callback = self._callback(data)
        async_to_sync(remote_checkout.checkout_callback_handler)(callback, self.bot)
        return callback

    def _reply(self, prompt_text, text, telegram_id=GUEST):
        prompt = SimpleNamespace(text=prompt_text, from_user=SimpleNamespace(is_bot=True))
        return SimpleNamespace(
            text=text, from_user=_user(telegram_id), reply_to_message=prompt, answer=AsyncMock()
        )

    def _saved_destination(self):
        GuestProfile.objects.filter(id=self.guest.id).update(
            shipping_carrier=Order.Carrier.NOVA_POSHTA,
            shipping_destination="Іваненко Іван, Київ, відділення 25",
        )


class GuestShippingFlowTests(ShippingBotFixture):
    def test_shipping_button_shows_the_terms_and_the_menu(self):
        message = SimpleNamespace(from_user=_user(), answer=AsyncMock())
        with patch("bot.handlers.order.send_menu_view", new=AsyncMock()) as menu:
            async_to_sync(order_handlers.shipping_handler)(message, self.bot)
        text = message.answer.call_args_list[0].args[0]
        self.assertIn("Нова Пошта, Укрпошта", text)
        menu.assert_awaited_once()

    def test_checkout_asks_for_the_carrier_then_the_destination(self):
        callback = self._callback("cart:checkout")
        async_to_sync(order_handlers.cart_checkout_callback)(callback, self.bot)
        data = _data(callback.message.answer.call_args.kwargs["reply_markup"])
        self.assertIn("rc:s:c:np", data)
        self.assertIn("rc:s:c:up", data)
        self.assertNotIn("rc:s:c:mm", data)

        chosen = self._press("rc:s:c:np")
        prompt = chosen.message.answer.call_args.args[0]
        self.assertTrue(prompt.startswith(remote_checkout.DESTINATION_PROMPT))
        self.assertIn("Нова Пошта", prompt)
        self.assertTrue(chosen.message.answer.call_args.kwargs["reply_markup"].force_reply)

    def test_answer_saves_the_destination_and_asks_about_postage(self):
        self._press("rc:s:c:np")
        reply = self._reply(f"{remote_checkout.DESTINATION_PROMPT} (Нова Пошта)", "Іван, Київ, 25")
        self.assertTrue(remote_checkout._is_destination_answer(reply))
        self.assertFalse(remote_checkout._is_address_answer(reply))
        async_to_sync(remote_checkout.destination_handler)(reply, self.bot)

        self.guest.refresh_from_db()
        self.assertEqual(self.guest.shipping_destination, "Іван, Київ, 25")
        data = _data(reply.answer.call_args.kwargs["reply_markup"])
        self.assertIn("rc:s:t:c", data)
        self.assertIn("rc:s:t:f", data)

    def test_saved_destination_is_offered(self):
        self._saved_destination()
        callback = self._callback("cart:checkout")
        async_to_sync(order_handlers.cart_checkout_callback)(callback, self.bot)
        text = callback.message.answer.call_args.args[0]
        self.assertIn("відділення 25", text)
        data = _data(callback.message.answer.call_args.kwargs["reply_markup"])
        self.assertIn("rc:s:a:ok", data)
        self.assertIn("rc:s:a:new", data)

    def test_summary_and_cash_on_delivery_order(self):
        self._saved_destination()
        choose = self._press("rc:s:t:c")
        self.assertIn(
            "rc:s:m:c:0:r", _data(choose.message.edit_text.call_args.kwargs["reply_markup"])
        )
        summary = self._press("rc:s:m:c:0:r")
        text = summary.message.edit_text.call_args.args[0]
        self.assertIn("Нова Пошта", text)
        self.assertIn("Пересылка: по тарифу перевозчика", text)
        self.assertIn("наложенным платежом", text)
        self.assertNotIn("Время", text)
        place = next(
            data
            for data in _data(summary.message.edit_text.call_args.kwargs["reply_markup"])
            if data.startswith("rc:s:p:")
        )
        self.assertLessEqual(len(place.encode()), 64)
        self._press(place)
        order = Order.objects.get()
        self.assertEqual(order.channel, Order.Channel.SHIPPING)
        self.assertEqual(order.shipping_cost, Order.ShippingCost.CARRIER)
        self.assertEqual(order.payment_timing, Order.PaymentTiming.ON_RECEIPT)


class StaffShippingTests(ShippingBotFixture):
    def setUp(self):
        super().setUp()
        cashier = User.objects.create_user(
            username="ship_bot_cashier", password="x", partner=self.partner,
            role=User.Role.CASHIER,
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=cashier,
            telegram_account=TelegramAccount.objects.create(telegram_id=STAFF),
        )
        self._saved_destination()
        self._press("rc:s:p:c:0:60000:60000:r")
        self.order = Order.objects.get()
        transition_order_status(order=self.order, to_status=Order.Status.ACCEPTED)
        transition_order_status(order=self.order, to_status=Order.Status.READY)

    def _card_data(self):
        order = Order.objects.select_related("partner", "guest__telegram_account").get(
            id=self.order.id
        )
        return staff._render_order_card(order), _data(staff._order_actions_keyboard(order))

    def test_card_shows_where_and_asks_for_the_tracking_number(self):
        text, data = self._card_data()
        self.assertIn("Нова Пошта", text)
        self.assertIn("відділення 25", text)
        self.assertIn(f"staffship:{self.order.public_id}", data)
        self.assertNotIn(f"stafforder:{self.order.public_id}:delivering", data)
        self.assertIn(f"staffrccod:{self.order.public_id}", data)
        self.assertNotIn(f"staffrccash:{self.order.public_id}", data)

    def test_tracking_number_reply_ships_the_order(self):
        callback = self._callback(f"staffship:{self.order.public_id}", telegram_id=STAFF)
        with patch.object(staff, "resolve_partner_for_bot_token", return_value=self.partner):
            async_to_sync(staff.staff_ship_callback_handler)(callback, self.bot)
        prompt = callback.message.answer.call_args.args[0]
        self.assertTrue(prompt.startswith(staff.TRACKING_PROMPT))
        self.assertIn(self.order.public_id, prompt)

        reply = self._reply(prompt, "20450000000001", telegram_id=STAFF)
        self.assertTrue(staff._is_tracking_answer(reply))
        with patch.object(staff, "resolve_partner_for_bot_token", return_value=self.partner):
            async_to_sync(staff.staff_tracking_number_handler)(reply, self.bot)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.DELIVERING)
        self.assertEqual(self.order.tracking_number, "20450000000001")

    def test_guest_reply_to_a_tracking_prompt_changes_nothing(self):
        prompt = f"{staff.TRACKING_PROMPT} #{self.order.public_id}"
        reply = self._reply(prompt, "20450000000001", telegram_id=GUEST)
        with patch.object(staff, "resolve_partner_for_bot_token", return_value=self.partner):
            async_to_sync(staff.staff_tracking_number_handler)(reply, self.bot)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.READY)

    def test_guest_confirms_receipt_without_a_table(self):
        self.order.refresh_from_db()
        Order.objects.filter(id=self.order.id).update(
            status=Order.Status.DELIVERING, tracking_number="20450000000001"
        )
        callback = self._callback(f"guestorderconfirm:{self.order.public_id}")
        async_to_sync(session.guest_order_confirm_callback)(callback, self.bot)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.COMPLETED)
        self.assertIn("подтвержд", callback.message.edit_text.call_args.args[0])


class ShippingNavigationTests(ShippingBotFixture):
    def test_shop_with_only_shipping_shows_the_cart_and_the_shipping_button(self):
        from bot.keyboards.main import build_main_keyboard
        from bot.services.content import BotContent
        from bot.services.navigation import resolve_guest_navigation_state

        content = BotContent.for_partner(self.partner)
        state = resolve_guest_navigation_state(
            partner_id=self.partner.id, telegram_id=GUEST, content=content
        )
        labels = [
            button.text
            for row in build_main_keyboard(content, navigation_state=state).keyboard
            for button in row
        ]
        self.assertIn("Отправка почтой", labels)
        self.assertIn(content.button_cart_label, labels)


class StaffCashOnDeliveryTests(StaffShippingTests):
    def test_cash_on_delivery_is_recorded_from_the_card(self):
        callback = self._callback(f"staffrccod:{self.order.public_id}", telegram_id=STAFF)
        with patch.object(staff, "resolve_partner_for_bot_token", return_value=self.partner):
            async_to_sync(staff.staff_receipt_payment_callback_handler)(callback, self.bot)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.paid_at)
        self.assertEqual(self.order.payment_method, Order.PaymentMethod.COD)
