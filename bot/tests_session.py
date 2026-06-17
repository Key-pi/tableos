from django.test import SimpleTestCase

from apps.orders.models import Order
from bot.handlers.session import _build_guest_order_card_text, _build_guest_session_view
from bot.keyboards.session import build_guest_session_overview_keyboard
from bot.services.content import BotContent


class SessionRenderingTests(SimpleTestCase):
    def _build_order_stub(
        self,
        *,
        public_id: str,
        status: str = Order.Status.ACCEPTED,
        total_amount: str = "120.00",
        paid_at=None,
        received_at=None,
    ):
        assigned_employee = None
        table = type("TableStub", (), {"number": 7})()
        return type(
            "OrderStub",
            (),
            {
                "public_id": public_id,
                "status": status,
                "total_amount": total_amount,
                "paid_at": paid_at,
                "received_at": received_at,
                "table": table,
                "assigned_employee_id": None,
                "assigned_employee": assigned_employee,
                "get_status_display": lambda self: status.title(),
            },
        )()

    def test_guest_session_view_collapses_long_order_list(self):
        partner = type("PartnerStub", (), {"name": "Session Venue"})()
        table = type("TableStub", (), {"number": 7})()
        session = type("SessionStub", (), {"table": table})()
        content = BotContent.defaults()
        orders = [
            self._build_order_stub(public_id=f"ORD{i:03d}")
            for i in range(1, 9)
        ]

        text, shortcuts = _build_guest_session_view(
            partner=partner,
            session=session,
            orders=orders,
            content=content,
        )

        self.assertIn("Незавершённых заказов: 8", text)
        self.assertIn("• И ещё заказов: 2", text)
        self.assertEqual(len(shortcuts), 8)

    def test_guest_order_card_hides_bill_hint_when_order_is_paid(self):
        paid_order = self._build_order_stub(
            public_id="PAID1234",
            paid_at=object(),
        )

        text = _build_guest_order_card_text(paid_order)

        self.assertNotIn("запросите счёт кнопкой", text)


class SessionKeyboardTests(SimpleTestCase):
    def test_session_overview_keyboard_keeps_refresh_button_without_orders(self):
        keyboard = build_guest_session_overview_keyboard(order_shortcuts=[])

        button_texts = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertEqual(button_texts, ["Обновить мой стол"])
