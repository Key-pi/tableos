"""The guest's bot in their language (catalog step 4, owner 2026-11-07): the
menu, the choice and the cart use the owner's translations; the staff's
quick sale keeps the main language."""

from asgiref.sync import async_to_sync

from apps.menu.models import MenuCategory, MenuItem, MenuItemVariant, Modifier, ModifierGroup
from apps.menu.options import choices_for
from apps.orders.models import Order
from apps.orders.services import add_menu_item_for_telegram_user, get_active_cart_for_telegram_user
from bot.handlers.order import _render_cart
from bot.keyboards.menu import build_cart_keyboard
from bot.keyboards.options import build_choice_keyboard, starting_pick
from core.i18n import texts
from tests.apps.menu.test_options import options_module
from tests.bot.test_guest_flow import GUEST, GuestFlowFixture, _buttons


class BotTranslationsFixture(GuestFlowFixture):
    def setUp(self):
        super().setUp()
        options_module(self.partner)
        self.item = MenuItem.objects.get(public_id="FLOW0001")  # Сырники
        self.item.translations = {"en": {"name": "Syrnyky", "description": "Cottage pancakes"}}
        self.item.description = "Домашні"
        self.item.save()
        category = MenuCategory.objects.get(pk=self.item.category_id)
        category.translations = {"en": {"name": "Breakfasts"}}
        category.save()
        self.three = MenuItemVariant.objects.create(
            item=self.item, name="3 шт", price="220.00", translations={"en": {"name": "3 pcs"}}
        )
        group = ModifierGroup.objects.create(
            partner=self.partner, name="Соус", translations={"en": {"name": "Sauce"}}
        )
        group.items.add(self.item)
        self.cream = Modifier.objects.create(
            group=group, name="Сметана", price="20.00", translations={"en": {"name": "Sour cream"}}
        )
        self._choose(Order.Channel.PICKUP)


class BotTranslationsTests(BotTranslationsFixture):
    def test_the_menu_in_english(self):
        with texts("en"):
            text, markup = self._menu()
        self.assertIn("Breakfasts", text)
        self.assertIn("Syrnyky", text)
        self.assertIn("Cottage pancakes", text)
        self.assertTrue(any("Syrnyky" in button.text for button in _buttons(markup)))
        with texts("uk"):
            text, _markup = self._menu()
        self.assertIn("Сырники", text)
        self.assertNotIn("Syrnyky", text)

    def test_the_choice_and_the_cart_in_english(self):
        choices = choices_for(self.item)
        with texts("en"):
            markup = build_choice_keyboard(
                self.item, choices, starting_pick(choices), back_callback="menucat:x"
            )
            labels = [button.text for button in _buttons(markup)]
        self.assertTrue(any("3 pcs" in label for label in labels), labels)
        self.assertTrue(any("Sauce" in label for label in labels), labels)
        self.assertTrue(any("Sour cream" in label for label in labels), labels)
        with texts("en"):
            add_menu_item_for_telegram_user(
                partner_id=self.partner.id, telegram_id=GUEST, item_public_id="FLOW0001",
                variant_id=self.three.id.hex, modifier_ids=[self.cream.id.hex],
            )
        cart = get_active_cart_for_telegram_user(partner_id=self.partner.id, telegram_id=GUEST)
        with texts("en"):
            self.assertIn("Syrnyky, 3 pcs (Sour cream) × 1", _render_cart(cart))
            keyboard = build_cart_keyboard(cart=cart, supports_cart=True)
        self.assertIn("➖ Syrnyky, 3 pcs (Sour cream)", [b.text for b in _buttons(keyboard)])
        with texts("uk"):
            self.assertIn("Сырники, 3 шт (Сметана) × 1", _render_cart(cart))

    def test_the_staff_choice_keeps_the_main_language(self):
        choices = choices_for(self.item)
        with texts("en"):
            markup = build_choice_keyboard(
                self.item, choices, starting_pick(choices), back_callback="staffsale:pback",
                staff=True,
            )
        labels = [button.text for button in _buttons(markup)]
        self.assertTrue(any("3 шт" in label for label in labels), labels)
        self.assertFalse(any("Sour cream" in label for label in labels), labels)

    def test_the_menu_renders_without_queries_for_names(self):
        # The cart is rendered in async code: its names never query.
        add_menu_item_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, item_public_id="FLOW0001",
            variant_id=self.three.id.hex, modifier_ids=[self.cream.id.hex],
        )
        cart = get_active_cart_for_telegram_user(partner_id=self.partner.id, telegram_id=GUEST)
        with self.assertNumQueries(0), texts("en"):
            text = async_to_sync(_async_render)(cart)
        self.assertIn("Syrnyky, 3 pcs (Sour cream)", text)


async def _async_render(cart):
    return _render_cart(cart)


class OrderNamesTests(BotTranslationsFixture):
    """An order keeps the main language for the staff; the guest's own order
    reads in the guest's language."""

    def test_the_guests_order_in_english_the_stored_name_in_ukrainian(self):
        from apps.menu.options import resolve_choice
        from apps.orders.services import create_order
        from apps.users.models import GuestProfile
        from bot.handlers.profile import _load_my_order, _render_my_order

        choice = resolve_choice(self.item, self.three.id.hex, [self.cream.id.hex])
        order = create_order(
            partner_id=self.partner.id,
            guest=GuestProfile.objects.get(partner=self.partner,
                                           telegram_account__telegram_id=GUEST),
            items=[{"menu_item_id": self.item.id, "item_name": choice.name,
                    "unit_price": choice.unit_price, "quantity": 1,
                    "variant_id": self.three.id, "modifiers": choice.snapshot()}],
            channel=Order.Channel.PICKUP, payment_timing=Order.PaymentTiming.ON_RECEIPT,
            notify_staff=False,
        )
        self.assertEqual(order.items.get().item_name, "Сырники, 3 шт (Сметана)")  # staff
        loaded = _load_my_order(self.partner.id, GUEST, order.public_id)
        with texts("en"):
            self.assertIn("Syrnyky, 3 pcs (Sour cream) × 1", _render_my_order(loaded))
