from datetime import datetime

from django.test import SimpleTestCase

from bot.handlers.profile import _render_profile_bonuses, _render_profile_summary
from bot.keyboards.profile import (
    build_profile_bonuses_keyboard,
    build_profile_summary_keyboard,
)
from bot.services.content import BotContent


class ProfileRenderingTests(SimpleTestCase):
    def _build_guest(self):
        telegram_account = type(
            "TelegramStub",
            (),
            {"username": "guest_profile", "telegram_id": 101},
        )()
        return type(
            "GuestStub",
            (),
            {
                "telegram_account": telegram_account,
                "customer_code": "ABC123",
                "loyalty_balance": 320,
                "first_visit_at": datetime(2026, 6, 1, 18, 30),
                "last_visit_at": datetime(2026, 6, 12, 19, 0),
            },
        )()

    def test_profile_summary_contains_only_profile_data(self):
        table = type("TableStub", (), {"number": 7})()
        session = type("SessionStub", (), {"table": table})()

        text = _render_profile_summary(
            partner_name="Night Owl",
            guest_profile=self._build_guest(),
            active_session=session,
        )

        self.assertIn("<b>Мой профиль</b>", text)
        self.assertIn("Код клиента: <code>ABC123</code>", text)
        self.assertNotIn("Активные программы", text)
        self.assertNotIn("Последние движения", text)

    def test_profile_bonuses_collapses_programs_and_paginates_history(self):
        guest = self._build_guest()
        active_programs = [
            type(
                "ProgramStub",
                (),
                {
                    "name": f"Program {index}",
                    "get_program_type_display": lambda self: "Percent",
                    "get_trigger_event_display": lambda self: "Visit",
                },
            )()
            for index in range(1, 7)
        ]
        recent_transactions = [
            type(
                "TransactionStub",
                (),
                {
                    "amount": index,
                    "program_id": True,
                    "program": type("ProgramRef", (), {"name": f"Program {index}"})(),
                    "created_at": datetime(2026, 6, 10, 12, index),
                },
            )()
            for index in range(1, 8)
        ]

        text, total_pages = _render_profile_bonuses(
            guest_profile=guest,
            active_programs=active_programs,
            recent_transactions=recent_transactions,
            page=1,
        )

        self.assertIn("<b>Бонусы</b>", text)
        self.assertIn("• И ещё программ: 2", text)
        self.assertIn("Страница истории: 2/2", text)
        self.assertEqual(total_pages, 2)


class ProfileKeyboardTests(SimpleTestCase):
    def test_summary_keyboard_uses_configurable_bonuses_label(self):
        keyboard = build_profile_summary_keyboard(
            bonuses_label=BotContent.defaults().button_profile_bonuses_label,
        )

        button_texts = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertEqual(button_texts, ["Бонусы"])

    def test_bonuses_keyboard_shows_pagination_and_back(self):
        keyboard = build_profile_bonuses_keyboard(page=1, total_pages=3)

        button_texts = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertIn("← Назад", button_texts)
        self.assertIn("Дальше →", button_texts)
        self.assertIn("К профилю", button_texts)
