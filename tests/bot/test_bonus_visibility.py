"""Bonuses are part of the base, but a venue without a working bonus
programme does not show them to the guest (PQ-001, owner's decision
2026-11-14): no bonus code in the greeting, no balance and no «Бонусы»
button in the profile, no balance in the Mini App. Spending is already
closed without a programme (``get_redeemable_bonus_amount`` gives 0)."""

from types import SimpleNamespace

from asgiref.sync import async_to_sync

from apps.bonuses.models import BonusProgram
from apps.tables.models import Table
from bot.handlers import profile as profile_handlers
from bot.handlers import start
from tests.bot.test_guest_flow import GuestFlowFixture, _data
from tests.webapp.test_guest_api import MiniAppFixture


def _programme(partner, *, is_active=True):
    return BonusProgram.objects.create(
        partner=partner, name="Кешбэк", program_type=BonusProgram.ProgramType.CASHBACK,
        percent="5.00", max_redeem_share="20.00", is_active=is_active,
    )


class GuestBotTests(GuestFlowFixture):
    def greeting(self, args=""):
        message = self._message("/start")
        async_to_sync(start.start_handler)(message, SimpleNamespace(args=args), self.bot)
        return message.answer.call_args_list[0].args[0]

    def profile(self):
        message = self._message()
        async_to_sync(profile_handlers.profile_handler)(message, self.bot)
        return (message.answer.call_args.args[0],
                _data(message.answer.call_args.kwargs["reply_markup"]))

    def test_without_a_programme_the_greeting_has_no_bonus_code(self):
        self.assertNotIn("код для бонусов", self.greeting())

    def test_a_switched_off_programme_counts_as_none(self):
        _programme(self.partner, is_active=False)
        self.assertNotIn("код для бонусов", self.greeting())
        text, buttons = self.profile()
        self.assertNotIn("Бонусов на счёте", text)
        self.assertNotIn("guestprofile:bonuses:0", buttons)

    def test_with_a_programme_the_greeting_has_the_bonus_code(self):
        _programme(self.partner)
        self.assertIn("Ваш код для бонусов", self.greeting())

    def test_at_the_table_the_same(self):
        table = Table.objects.create(partner=self.partner, number=3, qr_token="vis-3")
        self.assertNotIn("код для бонусов", self.greeting(table.deep_link_payload))

    def test_without_a_programme_the_profile_has_no_bonuses(self):
        text, buttons = self.profile()
        self.assertIn("Мой профиль", text)
        self.assertNotIn("Код для бонусов", text)
        self.assertNotIn("Бонусов на счёте", text)
        self.assertNotIn("guestprofile:bonuses:0", buttons)

    def test_with_a_programme_the_profile_shows_them(self):
        _programme(self.partner)
        text, buttons = self.profile()
        self.assertIn("Код для бонусов", text)
        self.assertIn("Бонусов на счёте", text)
        self.assertIn("guestprofile:bonuses:0", buttons)

    def test_an_old_bonuses_button_says_there_is_no_programme(self):
        callback = self._callback("guestprofile:bonuses:0")
        async_to_sync(profile_handlers.profile_bonuses_callback)(callback, self.bot)
        callback.message.edit_text.assert_not_called()
        self.assertIn("бонусной программы нет", callback.answer.call_args.args[0])


class MiniAppTests(MiniAppFixture):
    def test_without_a_programme_the_balance_is_not_sent(self):
        status, body = self.call("state")
        self.assertEqual(status, 200, body)
        self.assertIsNone(body["bonus_balance"])

    def test_with_a_programme_the_balance_is_sent(self):
        _programme(self.partner)
        status, body = self.call("state")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["bonus_balance"], "0.00")
