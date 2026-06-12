from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from bot.handlers.profile import (
    _render_bonus_summary,
    _render_profile_summary,
)
from bot.keyboards.profile import (
    build_profile_bonus_keyboard,
    build_profile_overview_keyboard,
)


class ProfileRenderingTests(SimpleTestCase):
    def test_profile_summary_keeps_bonus_history_out_of_main_card(self):
        guest_profile = SimpleNamespace(
            telegram_account=SimpleNamespace(username="guest_user", telegram_id=700001),
            customer_code="ABCD1234",
            loyalty_balance="120.00",
            first_visit_at=datetime(2026, 6, 1, 10, 0),
            last_visit_at=datetime(2026, 6, 12, 15, 0),
        )
        active_session = SimpleNamespace(table=SimpleNamespace(number=7))

        text = _render_profile_summary(
            partner_name="Test Venue",
            guest_profile=guest_profile,
            active_session=active_session,
            bonuses_button_label="История бонусов",
        )

        self.assertIn("<b>Мой профиль</b>", text)
        self.assertIn("Код клиента: <code>ABCD1234</code>", text)
        self.assertNotIn("История операций", text)
        self.assertNotIn("Активные программы", text)

    def test_bonus_summary_shows_compact_page_window(self):
        guest_profile = SimpleNamespace(
            customer_code="ZXCV0001",
            loyalty_balance="85.00",
        )
        active_programs = [
            SimpleNamespace(
                name="Cashback 5%",
                get_program_type_display=lambda: "Cashback",
                get_trigger_event_display=lambda: "Order completed",
            )
        ]
        transactions = [
            SimpleNamespace(
                amount=Decimal("15.00"),
                program=SimpleNamespace(name="Cashback 5%"),
                program_id="prog-1",
                get_transaction_type_display=lambda: "Accrual",
                created_at=datetime(2026, 6, 12, 12, 30),
            )
        ]

        text = _render_bonus_summary(
            partner_name="Test Venue",
            guest_profile=guest_profile,
            active_programs=active_programs,
            transactions=transactions,
            page=2,
            total_pages=3,
            total_transactions=11,
        )

        self.assertIn("<b>Бонусы</b>", text)
        self.assertIn("страница 2/3", text)
        self.assertIn("Показаны операции 6-6 из 11.", text)


class ProfileKeyboardTests(SimpleTestCase):
    def test_profile_overview_keyboard_shows_bonus_entry(self):
        keyboard = build_profile_overview_keyboard(
            bonuses_button_label="История бонусов",
        )

        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertEqual(button_texts, ["История бонусов"])

    def test_profile_bonus_keyboard_supports_navigation_and_back(self):
        keyboard = build_profile_bonus_keyboard(page=2, total_pages=4)

        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("← Назад", button_texts)
        self.assertIn("2/4", button_texts)
        self.assertIn("Дальше →", button_texts)
        self.assertIn("← К профилю", button_texts)
