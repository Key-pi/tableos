"""Guest and staff bot flows for pickup (docs/architecture/PICKUP.md)."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.billing.models import Bill
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Cart, Order
from apps.orders.services import add_items_to_cart_for_telegram_user
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.users.models import GuestProfile, TelegramAccount, User
from apps.users.services import get_or_create_guest_profile
from bot.handlers import order as order_handlers
from bot.handlers import remote_checkout, staff
from bot.handlers.menu import send_menu_view
from bot.handlers.start import start_handler
from bot.services.runtime import BotRuntimeConfig, register_runtime_configs

GUEST = 7400001
STAFF = 7400901


def _user(telegram_id):
    return SimpleNamespace(
        id=telegram_id, username="", first_name="G", last_name="", language_code=""
    )


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


class PickupBotFixture(TestCase):
    token = "pickup-bot-token"

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Pickup Bot Venue", slug="pickup-bot-venue", status=Partner.Status.ACTIVE
        )
        PartnerBotSettings.objects.create(
            partner=self.partner,
            module_menu_enabled=True,
            module_orders_enabled=True,
            module_cart_enabled=True,
            module_tables_enabled=True,
            module_pickup_enabled=True,
            transfer_payment_details="Карта 5375 0000 0000 0000 <Тест & Ко>",
        )
        bot_instance = BotInstance.objects.create(
            partner=self.partner,
            display_name="Pickup Bot",
            username="pickup_bot_venue_bot",
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
            partner=self.partner, category=category, public_id="PB000001", name="Суп",
            price="150.00",
        )
        self.guest = get_or_create_guest_profile(partner_id=self.partner.id, telegram_id=GUEST)
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, raw_items="PB000001x2"
        )
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

    def _press(self, data, handler=None):
        callback = self._callback(data)
        async_to_sync(handler or remote_checkout.checkout_callback_handler)(callback, self.bot)
        return callback

    def _set_phone(self):
        GuestProfile.objects.filter(id=self.guest.id).update(phone="+380671234567")


class GuestPickupFlowTests(PickupBotFixture):
    def test_checkout_without_table_asks_for_the_phone_once(self):
        callback = self._callback("cart:checkout")
        async_to_sync(order_handlers.cart_checkout_callback)(callback, self.bot)

        text = callback.message.answer.call_args.args[0]
        self.assertIn("номер телефона", text)
        markup = callback.message.answer.call_args.kwargs["reply_markup"]
        self.assertTrue(markup.keyboard[0][0].request_contact)

    def test_only_the_guests_own_contact_is_saved(self):
        def contact_message(user_id):
            return SimpleNamespace(
                from_user=_user(GUEST),
                contact=SimpleNamespace(user_id=user_id, phone_number="+380501112233"),
                answer=AsyncMock(),
            )

        foreign = contact_message(999)
        async_to_sync(remote_checkout.contact_handler)(foreign, self.bot)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.phone, "")

        own = contact_message(GUEST)
        async_to_sync(remote_checkout.contact_handler)(own, self.bot)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.phone, "+380501112233")
        time_markup = own.answer.call_args.kwargs["reply_markup"]
        self.assertIn("rc:p:t:now", [button.callback_data for button in _buttons(time_markup)])

    def test_summary_shows_requisites_and_placing_creates_the_order(self):
        self._set_phone()
        callback = self._press("rc:p:t:30")

        text = callback.message.edit_text.call_args.args[0]
        self.assertIn("К оплате переводом: 300.00 грн", text)
        self.assertIn("&lt;Тест &amp; Ко&gt;", text)
        buttons = _buttons(callback.message.edit_text.call_args.kwargs["reply_markup"])
        self.assertTrue(all(len(button.callback_data) <= 64 for button in buttons))
        paid = next(button for button in buttons if button.callback_data.startswith("rc:p:p:"))

        placed = self._press(paid.callback_data)

        order = Order.objects.get(guest=self.guest)
        self.assertEqual(order.channel, Order.Channel.PICKUP)
        self.assertIsNotNone(order.requested_for)
        self.assertIn(f"#{order.public_id}", placed.message.edit_text.call_args.args[0])

    def test_changed_cart_is_caught_before_ordering(self):
        self._set_phone()
        summary = self._press("rc:p:t:now")
        paid = next(
            button
            for button in _buttons(summary.message.edit_text.call_args.kwargs["reply_markup"])
            if button.callback_data.startswith("rc:p:p:")
        )
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, raw_items="PB000001x1"
        )

        callback = self._press(paid.callback_data)

        self.assertFalse(Order.objects.exists())
        self.assertIn("изменилась", callback.answer.call_args.args[0])
        self.assertIn("450.00", callback.message.edit_text.call_args.args[0])

    def test_bonus_step_offers_spending_bonuses(self):
        self._set_phone()
        BonusProgram.objects.create(
            partner=self.partner,
            name="Cashback",
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00",
            max_redeem_share="10.00",
        )
        BonusTransaction.objects.create(
            partner=self.partner,
            guest=self.guest,
            transaction_type=BonusTransaction.TransactionType.MANUAL,
            amount="100.00",
        )
        GuestProfile.objects.filter(id=self.guest.id).update(loyalty_balance=Decimal("100.00"))

        callback = self._press("rc:p:t:now")

        data = [b.callback_data for b in _buttons(callback.message.edit_text.call_args.kwargs[
            "reply_markup"
        ])]
        self.assertIn("rc:p:b:now:1", data)
        summary = self._press("rc:p:b:now:1")
        self.assertIn("К оплате переводом: 270.00 грн", summary.message.edit_text.call_args.args[0])

    def test_the_flow_is_chosen_by_buttons_not_at_checkout(self):
        hookah = MenuItem.objects.create(
            partner=self.partner,
            category=MenuCategory.objects.get(partner=self.partner),
            public_id="PBHOOKAH",
            name="Кальян",
            price="500.00",
            available_for_pickup=False,
        )
        table = Table.objects.create(partner=self.partner, number=2, qr_token="pickup-bot-t2")
        scan = SimpleNamespace(from_user=_user(GUEST), answer=AsyncMock())
        async_to_sync(start_handler)(
            scan, SimpleNamespace(args=table.deep_link_payload), self.bot
        )
        # Scanning the QR chose the table flow; the soup stays in the cart.
        cart = Cart.objects.get(guest=self.guest, status=Cart.Status.ACTIVE)
        self.assertEqual(cart.channel, Order.Channel.TABLE)
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, raw_items="PBHOOKAHx1"
        )

        switch = self._press("cart:mode:pickup", handler=order_handlers.cart_mode_callback)

        text = switch.message.edit_text.call_args.args[0]
        self.assertIn("самовывоз", text)
        self.assertIn("Кальян", text)  # reported as removed
        cart.refresh_from_db()
        self.assertEqual(cart.channel, Order.Channel.PICKUP)
        self.assertFalse(cart.items.filter(menu_item=hookah).exists())

        checkout = self._callback("cart:checkout")
        async_to_sync(order_handlers.cart_checkout_callback)(checkout, self.bot)
        self.assertIn("номер телефона", checkout.message.answer.call_args.args[0])

        # No «к столу» button: the table flow starts only from the QR code.
        markup = switch.message.edit_text.call_args.kwargs["reply_markup"]
        self.assertFalse(
            [b for b in _buttons(markup) if b.callback_data.startswith("cart:mode:")]
        )
        rescan = SimpleNamespace(from_user=_user(GUEST), answer=AsyncMock())
        async_to_sync(start_handler)(
            rescan, SimpleNamespace(args=table.deep_link_payload), self.bot
        )
        cart.refresh_from_db()
        self.assertEqual(cart.channel, Order.Channel.TABLE)

        # No «к столу» button: the table flow starts only from the QR code.
        markup = switch.message.edit_text.call_args.kwargs["reply_markup"]
        self.assertFalse(
            [b for b in _buttons(markup) if b.callback_data.startswith("cart:mode:")]
        )
        rescan = SimpleNamespace(from_user=_user(GUEST), answer=AsyncMock())
        async_to_sync(start_handler)(
            rescan, SimpleNamespace(args=table.deep_link_payload), self.bot
        )
        cart.refresh_from_db()
        self.assertEqual(cart.channel, Order.Channel.TABLE)

    def test_pickup_menu_hides_table_only_items(self):
        MenuItem.objects.create(
            partner=self.partner,
            category=MenuCategory.objects.get(partner=self.partner),
            public_id="PBHOOK02",
            name="Кальян",
            price="500.00",
            available_for_pickup=False,
        )
        message = SimpleNamespace(from_user=_user(GUEST), answer=AsyncMock())
        async_to_sync(send_menu_view)(target=message, bot=self.bot)

        text = message.answer.call_args.args[0]
        self.assertIn("Суп", text)
        self.assertNotIn("Кальян", text)


class StaffPickupPaymentTests(PickupBotFixture):
    def setUp(self):
        super().setUp()
        self._set_phone()
        cashier = User.objects.create_user(
            username="pickup_bot_cashier", password="x", partner=self.partner,
            role=User.Role.CASHIER,
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=cashier,
            telegram_account=TelegramAccount.objects.create(telegram_id=STAFF),
        )
        summary = self._press("rc:p:t:now")
        paid = next(
            button
            for button in _buttons(summary.message.edit_text.call_args.kwargs["reply_markup"])
            if button.callback_data.startswith("rc:p:p:")
        )
        self._press(paid.callback_data)
        self.order = Order.objects.get(guest=self.guest)

    def _staff(self, data):
        callback = self._callback(data, telegram_id=STAFF)
        with patch.object(staff, "resolve_partner_for_bot_token", return_value=self.partner):
            async_to_sync(staff.staff_transfer_check_callback_handler)(callback, self.bot)
        return callback

    def test_card_offers_payment_check_instead_of_accepting(self):
        markup = staff._order_actions_keyboard(
            Order.objects.select_related("partner", "guest").get(id=self.order.id)
        )
        data = [button.callback_data for button in _buttons(markup)]
        self.assertIn(f"stafftxok:{self.order.public_id}", data)
        self.assertNotIn(f"stafforder:{self.order.public_id}:accepted", data)

    def test_confirm_takes_the_order_into_work(self):
        callback = self._staff(f"stafftxok:{self.order.public_id}")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.ACCEPTED)
        self.assertIsNotNone(self.order.paid_at)
        self.assertIn("подтверждена", callback.answer.call_args.args[0])

    def test_reject_asks_first_then_cancels(self):
        ask = self._staff(f"stafftxno:{self.order.public_id}")
        self.assertIn("Отменить заказ", ask.message.edit_text.call_args.args[0])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.NEW)

        self._staff(f"stafftxnoy:{self.order.public_id}")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELED)
        self.assertTrue(Bill.objects.filter(status=Bill.Status.CANCELED).exists())
