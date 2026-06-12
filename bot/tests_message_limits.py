from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from bot.handlers.order import _render_cart
from bot.handlers.session import _build_guest_session_view
from bot.handlers.staff import _render_order_card


class MessagePreviewLimitTests(SimpleTestCase):
    def test_cart_preview_collapses_extra_items(self):
        items = [
            SimpleNamespace(
                item_name=f"Item {index}",
                quantity=1,
                unit_price=Decimal("10.00"),
            )
            for index in range(14)
        ]
        cart = SimpleNamespace(
            table_session=SimpleNamespace(table=SimpleNamespace(number=3)),
            items=SimpleNamespace(all=lambda: items),
            total_amount=Decimal("140.00"),
        )

        text = _render_cart(cart)

        self.assertIn("И ещё 2 позиц.", text)

    def test_guest_session_preview_collapses_extra_orders(self):
        partner = SimpleNamespace(name="Venue")
        session = SimpleNamespace(table=SimpleNamespace(number=2))
        content = SimpleNamespace(ordering_entry_lines=lambda table_number: [])

        def _order(public_id: str):
            return SimpleNamespace(
                public_id=public_id,
                total_amount=Decimal("50.00"),
                status="accepted",
                get_status_display=lambda: "Accepted",
                paid_at=None,
                received_at=None,
            )

        orders = [_order(f"ord{index}") for index in range(10)]

        text, _shortcuts = _build_guest_session_view(
            partner=partner,
            session=session,
            orders=orders,
            content=content,
        )

        self.assertIn("И ещё 2 заказ(а).", text)

    def test_staff_order_card_collapses_extra_positions(self):
        items = [
            SimpleNamespace(item_name=f"Dish {index}", quantity=1)
            for index in range(8)
        ]
        order = SimpleNamespace(
            public_id="abcd1234",
            items=SimpleNamespace(all=lambda: items),
            guest=SimpleNamespace(telegram_account=SimpleNamespace(username="guest", telegram_id=1)),
            comment="",
            assigned_employee=None,
            total_amount=Decimal("320.00"),
            table=SimpleNamespace(number=5),
            status="new",
            get_status_display=lambda: "New",
            paid_at=None,
            received_at=None,
        )

        text = _render_order_card(order)

        self.assertIn("и ещё 2", text)
