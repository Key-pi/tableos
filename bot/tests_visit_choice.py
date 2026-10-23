from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.partners.models import BotInstance, Partner
from apps.tables.models import Table, TableSession
from apps.tables.services import activate_table_session
from bot.handlers.start import start_handler, visit_choice_callback
from bot.services.runtime import BotRuntimeConfig, register_runtime_configs


def _user(telegram_id):
    return SimpleNamespace(
        id=telegram_id, username="", first_name="Guest", last_name="", language_code=""
    )


class VisitChoiceBotFlowTests(TestCase):
    """ADR-0007: the second party at a table chooses join or new visit."""

    token = "visit-choice-token"

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Choice Venue", slug="choice-venue", status=Partner.Status.ACTIVE
        )
        bot_instance = BotInstance.objects.create(
            partner=self.partner,
            display_name="Choice Bot",
            username="choice_venue_bot",
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
        self.table = Table.objects.create(partner=self.partner, number=8, qr_token="choice08")
        self.first = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=880001,
            payload=self.table.deep_link_payload,
        )
        self.bot = SimpleNamespace(token=self.token)

    def tearDown(self):
        register_runtime_configs([])

    def _scan(self, telegram_id):
        message = SimpleNamespace(from_user=_user(telegram_id), answer=AsyncMock())
        command = SimpleNamespace(args=self.table.deep_link_payload)
        async_to_sync(start_handler)(message, command, self.bot)
        return message

    def _press(self, telegram_id, callback_data):
        callback = SimpleNamespace(
            data=callback_data,
            from_user=_user(telegram_id),
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock(), edit_reply_markup=AsyncMock()),
        )
        async_to_sync(visit_choice_callback)(callback, self.bot)
        return callback

    def _buttons(self, message):
        markup = message.answer.call_args.kwargs["reply_markup"]
        return [button for row in markup.inline_keyboard for button in row]

    def test_second_guest_is_asked_and_joins_the_party(self):
        message = self._scan(880002)

        self.assertIn("уже сидит компания", message.answer.call_args.args[0])
        buttons = self._buttons(message)
        join = next(button for button in buttons if button.callback_data.startswith("tv:j:"))
        self.assertIn("гостей: 1", join.text)
        self.assertTrue(all(len(button.callback_data) <= 64 for button in buttons))
        self.assertFalse(TableSession.objects.filter(guest__telegram_account__telegram_id=880002))

        callback = self._press(880002, join.callback_data)

        session = TableSession.objects.get(
            guest__telegram_account__telegram_id=880002, status=TableSession.Status.ACTIVE
        )
        self.assertEqual(session.visit_id, self.first.visit_id)
        callback.message.answer.assert_awaited()

    def test_second_guest_can_start_a_new_visit(self):
        message = self._scan(880003)
        new = next(
            button for button in self._buttons(message) if button.callback_data.startswith("tv:n:")
        )

        self._press(880003, new.callback_data)

        session = TableSession.objects.get(
            guest__telegram_account__telegram_id=880003, status=TableSession.Status.ACTIVE
        )
        self.assertNotEqual(session.visit_id, self.first.visit_id)

    def test_forged_callback_is_rejected(self):
        callback = self._press(880004, "tv:j:" + "0" * 32)

        callback.answer.assert_awaited_with(
            "Выбор устарел. Отсканируйте QR-код стола ещё раз.", show_alert=True
        )
        self.assertFalse(TableSession.objects.filter(guest__telegram_account__telegram_id=880004))
