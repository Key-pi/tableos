from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from bot.handlers.order import checkout_handler
from bot.services.content import BotContent


class CheckoutReplyButtonTests(SimpleTestCase):
    def _run_checkout(self, label: str) -> MagicMock:
        content = BotContent.defaults()
        content.button_checkout_label = label
        partner = SimpleNamespace(id="partner-id", name="Venue")
        order = SimpleNamespace(
            public_id="ABCD1234",
            total_amount="150.00",
            get_status_display=lambda: "New",
        )
        checkout = MagicMock(return_value=order)
        message = SimpleNamespace(
            text=label,
            from_user=SimpleNamespace(id=777),
            answer=AsyncMock(),
        )
        bot = SimpleNamespace(token="token")
        navigation = SimpleNamespace(has_action=lambda _action: False, available_actions=())

        with (
            patch("bot.handlers.order.resolve_partner_for_bot_token", return_value=partner),
            patch("bot.handlers.order.BotContent.for_partner", return_value=content),
            patch("bot.handlers.order.resolve_guest_navigation_state", return_value=navigation),
            patch("bot.handlers.order.build_main_keyboard", return_value=None),
            patch("bot.handlers.order.checkout_active_cart_for_telegram_user", checkout),
        ):
            async_to_sync(checkout_handler)(message, bot)
        return checkout

    def test_reply_button_checkout_never_stores_the_label_as_comment(self):
        # AA-027: the default label "Оформить заказ" used to become comment "заказ".
        for label in ("Оформить заказ", "Оформить | без льда", "Checkout"):
            checkout = self._run_checkout(label)
            checkout.assert_called_once()
            self.assertEqual(checkout.call_args.kwargs["comment"], "", label)
