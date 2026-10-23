import re
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.orders.models import Order
from bot.handlers.menu import _render_menu_category_view
from bot.handlers.staff import _render_order_card, _render_staff_notification_card
from bot.services.content import BotContent
from bot.utils.html import escape_html

_ALLOWED_TAG = re.compile(r"</?(b|i|code)>")
_ENTITY = re.compile(r"&(amp|lt|gt|quot|#\d+);")
NASTY = "Bar & Grill <VIP>"


def assert_valid_telegram_html(test, text: str) -> None:
    """Mimic Telegram's HTML parser: only known tags, no bare & < >."""

    stripped = _ALLOWED_TAG.sub("", text)
    test.assertNotIn("<", stripped, text)
    test.assertNotIn(">", stripped, text)
    test.assertEqual(stripped.count("&"), len(_ENTITY.findall(stripped)), text)


class EscapeHtmlTests(SimpleTestCase):
    def test_escape_html_handles_none_and_specials(self):
        self.assertEqual(escape_html(None), "")
        self.assertEqual(escape_html(NASTY), "Bar &amp; Grill &lt;VIP&gt;")
        self.assertEqual(escape_html('say "hi"'), 'say "hi"')

    def test_partner_template_keeps_markup_but_escapes_values(self):
        content = BotContent.defaults()
        header = content.menu_header(partner_name=NASTY)
        self.assertTrue(header.startswith("<b>"))
        self.assertIn("Bar &amp; Grill &lt;VIP&gt;", header)
        assert_valid_telegram_html(self, header)
        assert_valid_telegram_html(self, content.welcome_message(partner_name=NASTY))
        assert_valid_telegram_html(
            self,
            content.table_activated_message(partner_name=NASTY, table_number=3),
        )


class GuestAndStaffRenderingTests(SimpleTestCase):
    def test_menu_category_view_escapes_venue_and_menu_text(self):
        category = SimpleNamespace(name="Cocktails & <Shots>")
        item = SimpleNamespace(
            name="Rum & Cola <0.5>",
            price=Decimal("150.00"),
            category_id=1,
            category=category,
            description="Ice < 3 cubes & lime",
        )
        text = _render_menu_category_view(
            partner=SimpleNamespace(name=NASTY),
            content=BotContent.defaults(),
            category=category,
            menu_items=[item],
            session=SimpleNamespace(table=SimpleNamespace(number=1)),
        )
        assert_valid_telegram_html(self, text)
        self.assertIn("Rum &amp; Cola &lt;0.5&gt;", text)

    def test_staff_order_card_escapes_items_and_comment(self):
        order = SimpleNamespace(
            public_id="ABCD1234",
            table_id=1,
            table=SimpleNamespace(number=4),
            guest=SimpleNamespace(telegram_account=SimpleNamespace(username="", telegram_id=42)),
            assigned_employee=SimpleNamespace(
                user=SimpleNamespace(get_full_name=lambda: "Ann & <Bob>", username="ann")
            ),
            status=Order.Status.NEW,
            paid_at=None,
            received_at=None,
            total_amount=Decimal("10.00"),
            comment="No ice <please> & fast",
            items=SimpleNamespace(
                all=lambda: [SimpleNamespace(item_name="Tea & <Milk>", quantity=1)]
            ),
            get_status_display=lambda: "New",
        )
        assert_valid_telegram_html(self, _render_order_card(order))

    def test_staff_notification_card_escapes_title_and_message(self):
        notification = SimpleNamespace(
            title="Вызов: Бар & <касса>",
            message=f"Заведение: {NASTY}",
            order_id=None,
            order=None,
        )
        assert_valid_telegram_html(self, _render_staff_notification_card(notification))
