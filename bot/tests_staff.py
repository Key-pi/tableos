from django.test import SimpleTestCase

from apps.orders.models import Order
from apps.orders.services import OrderFlowError
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
from bot.handlers.staff import (
    _build_order_shortcuts,
    _build_unbilled_order_shortcuts,
    _is_staff_overview_message,
    _render_billing_overview,
    _render_order_card,
    _render_staff_notifications,
    _require_staff_sale_preview_state,
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

    def test_billing_overview_keyboard_supports_off_table_shortcuts_without_table_button(self):
        keyboard = build_staff_billing_overview_keyboard(
            unbilled_order_shortcuts=[
                ("abcd1234", "К оплате #abcd1234 • Вне стола • Получен • 650.00 грн", None)
            ],
        )

        button_texts = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("К оплате #abcd1234 • Вне стола • Получен • 650.00 грн", button_texts)
        self.assertNotIn("Стол", button_texts)

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


class StaffOrderRenderingTests(SimpleTestCase):
    def _build_order_stub(self, *, table_number=None):
        guest = type(
            "Guest",
            (),
            {"telegram_account": type("Telegram", (), {"username": "guest_one", "telegram_id": 1})()},
        )()
        table = None
        table_id = None
        if table_number is not None:
            table = type("Table", (), {"number": table_number})()
            table_id = "table-id"
        assigned_employee = None
        items = [type("Item", (), {"item_name": "Tea", "quantity": 2})()]
        return type(
            "OrderStub",
            (),
            {
                "public_id": "abcd1234",
                "guest": guest,
                "assigned_employee": assigned_employee,
                "status": Order.Status.NEW,
                "total_amount": "200.00",
                "comment": "",
                "table": table,
                "table_id": table_id,
                "items": type("Items", (), {"all": lambda self: items})(),
                "get_status_display": lambda self: "New",
                "paid_at": None,
                "received_at": None,
            },
        )()

    def test_render_order_card_handles_order_without_table(self):
        order = self._build_order_stub(table_number=None)

        text = _render_order_card(order)

        self.assertIn("Сценарий: заказ без привязки к столу", text)
        self.assertIn("Заказ #abcd1234", text)

    def test_build_order_shortcuts_marks_order_without_table(self):
        order = self._build_order_stub(table_number=None)

        shortcuts = _build_order_shortcuts([order])

        self.assertEqual(shortcuts[0][0], "abcd1234")
        self.assertIn("Вне стола", shortcuts[0][1])


class StaffQuickSaleStateTests(SimpleTestCase):
    def test_require_staff_sale_preview_state_rejects_stale_state(self):
        with self.assertRaisesMessage(
            OrderFlowError,
            "Черновик быстрой продажи устарел. Начните заново.",
        ):
            _require_staff_sale_preview_state({})

    def test_require_staff_sale_preview_state_returns_normalized_payload(self):
        payload = _require_staff_sale_preview_state(
            {
                "preview_chat_id": 10,
                "preview_message_id": 20,
                "customer_code": "ABC123",
                "items": {"item-1": 2},
                "selected_category_id": "cat-1",
                "comment": "No sugar",
                "redeem_bonus": True,
            }
        )

        self.assertEqual(payload["preview_chat_id"], 10)
        self.assertEqual(payload["preview_message_id"], 20)
        self.assertEqual(payload["customer_code"], "ABC123")
        self.assertEqual(payload["items"], {"item-1": 2})
        self.assertTrue(payload["redeem_bonus"])


class StaffBillingHelperTests(SimpleTestCase):
    def _build_order_stub(self, *, public_id: str, table_number=None, status=Order.Status.READY):
        table = None
        table_id = None
        if table_number is not None:
            table = type("Table", (), {"number": table_number})()
            table_id = f"table-{table_number}"
        return type(
            "OrderStub",
            (),
            {
                "public_id": public_id,
                "table": table,
                "table_id": table_id,
                "status": status,
                "total_amount": "300.00",
                "get_status_display": lambda self: status.title(),
            },
        )()

    def test_build_unbilled_order_shortcuts_keeps_off_table_orders(self):
        shortcuts = _build_unbilled_order_shortcuts(
            [
                self._build_order_stub(public_id="table1234", table_number=4),
                self._build_order_stub(public_id="off12345", table_number=None),
            ]
        )

        labels = [label for _public_id, label, _table_id in shortcuts]
        self.assertTrue(any("T4" in label for label in labels))
        self.assertTrue(any("Вне стола" in label for label in labels))

    def test_render_billing_overview_mentions_off_table_unbilled_orders(self):
        text = _render_billing_overview(
            requests=[],
            bills=[],
            unbilled_orders=[
                self._build_order_stub(public_id="table1234", table_number=4),
                self._build_order_stub(public_id="off12345", table_number=None),
            ],
        )

        self.assertIn("Неоплаченных заказов без счёта: 2", text)
        self.assertIn("Из них без привязки к столу: 1", text)


class StaffOverviewMessageTests(SimpleTestCase):
    def test_is_staff_overview_message_recognizes_billing_overview(self):
        message = type("MessageStub", (), {"html_text": "<b>Счета и оплаты</b>\nОткрытых счетов: 2"})()

        self.assertTrue(_is_staff_overview_message(message, "Счета и оплаты"))

    def test_is_staff_overview_message_recognizes_notifications_overview(self):
        text = _render_staff_notifications([object()])
        message = type("MessageStub", (), {"html_text": text})()

        self.assertTrue(_is_staff_overview_message(message, "Уведомления персоналу"))

    def test_is_staff_overview_message_rejects_other_messages(self):
        message = type("MessageStub", (), {"html_text": "<b>Заказ #1234</b>"})()

        self.assertFalse(_is_staff_overview_message(message, "Счета и оплаты"))
