from django.test import SimpleTestCase

from bot.handlers.menu import _render_menu_category_view, _trim_menu_description
from bot.handlers.order import _render_cart
from bot.keyboards.menu import build_cart_keyboard, build_menu_keyboard
from bot.services.content import BotContent


class MenuRenderingTests(SimpleTestCase):
    def _build_partner(self):
        return type("PartnerStub", (), {"name": "Night Owl"})()

    def _build_category(self):
        return type("CategoryStub", (), {"id": "cat-1", "name": "Коктейли"})()

    def _build_session(self):
        table = type("TableStub", (), {"number": 12})()
        return type("SessionStub", (), {"table": table})()

    def test_render_menu_category_view_shows_empty_category_hint(self):
        text = _render_menu_category_view(
            partner=self._build_partner(),
            content=BotContent.defaults(),
            category=self._build_category(),
            menu_items=[],
            session=None,
        )

        self.assertIn("В этой категории сейчас нет доступных позиций.", text)

    def test_render_menu_category_view_trims_overlong_description(self):
        category = self._build_category()
        item = type(
            "ItemStub",
            (),
            {
                "name": "Авторский лимонад",
                "price": "180.00",
                "description": "Очень длинное описание " * 30,
                "category": category,
                "category_id": category.id,
            },
        )()

        text = _render_menu_category_view(
            partner=self._build_partner(),
            content=BotContent.defaults(),
            category=category,
            menu_items=[item],
            session=self._build_session(),
        )

        self.assertIn("Авторский лимонад", text)
        self.assertIn("…", text)

    def test_trim_menu_description_keeps_short_text_unchanged(self):
        self.assertEqual(_trim_menu_description("Свежий лайм"), "Свежий лайм")


class MenuKeyboardTests(SimpleTestCase):
    def test_keyboard_hides_add_buttons_without_session(self):
        category = type("CategoryStub", (), {"id": "cat-1", "name": "Кофе"})()
        item = type("ItemStub", (), {"name": "Эспрессо", "price": "90.00", "public_id": "COF001"})()

        keyboard = build_menu_keyboard(
            categories=[category],
            active_category_id=category.id,
            items=[item],
            has_session=False,
            supports_cart=True,
        )

        button_texts = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertIn("• Кофе", button_texts)
        self.assertNotIn("➕ Эспрессо • 90.00 грн", button_texts)
        self.assertNotIn("Открыть корзину", button_texts)

    def test_cart_keyboard_hides_checkout_actions_for_empty_cart(self):
        empty_cart = type("CartStub", (), {"items": type("Items", (), {"all": lambda self: []})()})()

        keyboard = build_cart_keyboard(cart=empty_cart, supports_cart=True)

        button_texts = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertNotIn("Оформить", button_texts)
        self.assertNotIn("Очистить", button_texts)
        self.assertIn("Обновить корзину", button_texts)


class CartRenderingTests(SimpleTestCase):
    def test_render_cart_collapses_long_item_list(self):
        table = type("TableStub", (), {"number": 9})()
        session = type("SessionStub", (), {"table": table})()
        items = [
            type(
                "CartItemStub",
                (),
                {
                    "item_name": f"Item {index}",
                    "quantity": 1,
                    "unit_price": "100.00",
                },
            )()
            for index in range(1, 9)
        ]
        cart = type(
            "CartStub",
            (),
            {
                "table_session": session,
                "total_amount": "800.00",
                "items": type(
                    "Items",
                    (),
                    {
                        "all": lambda self: items,
                        "count": lambda self: len(items),
                    },
                )(),
            },
        )()

        text = _render_cart(cart)

        self.assertIn("<b>Корзина</b> • стол #9", text)
        self.assertIn("И ещё позиций: 2", text)
