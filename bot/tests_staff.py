from django.test import SimpleTestCase

from bot.keyboards.staff import (
    build_staff_billing_overview_keyboard,
    build_staff_home_keyboard,
    build_staff_notification_item_keyboard,
    build_staff_order_actions_keyboard,
    build_staff_orders_overview_keyboard,
    build_staff_report_section_keyboard,
    build_staff_report_summary_keyboard,
    build_staff_report_table_detail_keyboard,
)


class StaffKeyboardVisibilityTests(SimpleTestCase):
    def test_home_keyboard_hides_quick_sale_when_not_allowed(self):
        keyboard = build_staff_home_keyboard(can_quick_sale=False)

        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertNotIn("Быстрая продажа", button_texts)

    def test_orders_overview_keyboard_shows_quick_sale_when_allowed(self):
        keyboard = build_staff_orders_overview_keyboard(can_quick_sale=True)

        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("Быстрая продажа", button_texts)

    def test_orders_overview_keyboard_shows_order_shortcuts(self):
        keyboard = build_staff_orders_overview_keyboard(
            order_shortcuts=[
                ("abcd1234", "#abcd1234 • T2 • Новый • не опл.", True),
                ("efgh5678", "#efgh5678 • T6 • Готовим • опл.", False),
            ]
        )

        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("#abcd1234 • T2 • Новый • не опл.", button_texts)
        self.assertIn("Взять в работу", button_texts)
        self.assertIn("#efgh5678 • T6 • Готовим • опл.", button_texts)

    def test_home_keyboard_shows_billing_button_when_allowed(self):
        keyboard = build_staff_home_keyboard(can_manage_billing=True)

        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("Счета и оплаты", button_texts)

    def test_order_actions_keyboard_splits_buttons_into_readable_rows(self):
        keyboard = build_staff_order_actions_keyboard(
            order_public_id="abcd1234",
            available_statuses=["accepted", "ready", "canceled"],
        )

        self.assertEqual(len(keyboard.inline_keyboard[0]), 2)
        self.assertEqual(len(keyboard.inline_keyboard[1]), 1)
        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("Готово", button_texts)

    def test_notification_keyboard_shows_accept_action_for_new_order(self):
        keyboard = build_staff_notification_item_keyboard(
            notification_id="notif-1",
            category="order_created",
            order_public_id="abcd1234",
        )

        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("Взять в работу", button_texts)
        self.assertIn("Открыть заказ", button_texts)

    def test_billing_overview_keyboard_shows_unbilled_shortcuts(self):
        keyboard = build_staff_billing_overview_keyboard(
            unbilled_order_shortcuts=[
                ("abcd1234", "К оплате #abcd1234 • T2 • Получен • 650.00 грн", "table-1")
            ],
        )

        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("К оплате #abcd1234 • T2 • Получен • 650.00 грн", button_texts)
        self.assertIn("Стол", button_texts)

    def test_report_summary_keyboard_shows_journal_sections(self):
        keyboard = build_staff_report_summary_keyboard()

        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("Счета", button_texts)
        self.assertIn("Быстрые продажи", button_texts)
        self.assertIn("Столы", button_texts)
        self.assertIn("Хвосты", button_texts)

    def test_report_section_keyboard_supports_item_shortcuts_and_back(self):
        keyboard = build_staff_report_section_keyboard(
            section="bills",
            page=1,
            total_pages=2,
            item_shortcuts=[("Открыть счёт #abc", "staffbillopen:abc")],
        )

        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("Открыть счёт #abc", button_texts)
        self.assertIn("Дальше →", button_texts)
        self.assertIn("К сводке дня", button_texts)

    def test_report_table_detail_keyboard_supports_return_navigation(self):
        keyboard = build_staff_report_table_detail_keyboard(page=2)

        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("К списку столов", button_texts)
        self.assertIn("К сводке дня", button_texts)
