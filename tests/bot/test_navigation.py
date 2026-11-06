from django.test import SimpleTestCase, TestCase

from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Cart, CartItem, Order
from apps.partners.models import Partner
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import GuestProfile, TelegramAccount
from bot.keyboards.main import build_main_keyboard
from bot.services.content import BotContent
from bot.services.navigation import (
    GuestAction,
    GuestJourney,
    GuestNavigationState,
    resolve_guest_navigation_state,
)


class MainKeyboardStateTests(SimpleTestCase):
    def test_browse_state_hides_table_only_actions(self):
        content = BotContent.defaults()
        keyboard = build_main_keyboard(
            content,
            navigation_state=GuestNavigationState(
                journey=GuestJourney.BROWSE,
                available_actions=(
                    GuestAction.MENU,
                    GuestAction.PROFILE,
                    GuestAction.HELP,
                ),
            ),
        )

        button_texts = [
            button.text
            for row in keyboard.keyboard
            for button in row
        ]
        self.assertEqual(
            button_texts,
            [
                content.button_menu_label,
                content.button_my_profile,
                content.button_help_label,
            ],
        )


class GuestNavigationResolverTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Navigation Venue",
            slug="navigation-venue",
            status=Partner.Status.ACTIVE,
        )
        self.telegram_account = TelegramAccount.objects.create(
            telegram_id=550001,
            username="nav_guest",
        )
        self.guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=self.telegram_account,
        )
        self.table = Table.objects.create(
            partner=self.partner,
            number=2,
            name="Table 2",
            qr_token="navtable2",
        )
        self.content = BotContent.defaults()
        self.content.module_billing_enabled = True
        self.content.module_staff_call_enabled = True
        self.category = MenuCategory.objects.create(
            partner=self.partner,
            name="Drinks",
            sort_order=10,
        )
        self.menu_item = MenuItem.objects.create(
            partner=self.partner,
            category=self.category,
            public_id="NAV001",
            name="Lemonade",
            price="180.00",
            sort_order=10,
        )

    def test_resolver_returns_browse_state_without_session(self):
        state = resolve_guest_navigation_state(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            content=self.content,
        )

        self.assertEqual(state.journey, GuestJourney.BROWSE)
        self.assertFalse(state.has_active_session)
        self.assertFalse(state.has_action(GuestAction.SESSION))
        self.assertFalse(state.has_action(GuestAction.CART))
        self.assertTrue(state.has_action(GuestAction.MENU))
        self.assertTrue(state.has_action(GuestAction.PROFILE))

    def test_resolver_exposes_delivery_and_pickup_modules_without_session(self):
        self.content.module_delivery_enabled = True
        self.content.module_pickup_enabled = True

        state = resolve_guest_navigation_state(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            content=self.content,
        )
        keyboard = build_main_keyboard(self.content, navigation_state=state)
        button_texts = [button.text for row in keyboard.keyboard for button in row]

        self.assertEqual(state.journey, GuestJourney.BROWSE)
        self.assertTrue(state.has_action(GuestAction.DELIVERY))
        self.assertTrue(state.has_action(GuestAction.PICKUP))
        self.assertIn(self.content.button_delivery_label, button_texts)
        self.assertIn(self.content.button_pickup_label, button_texts)

    def test_resolver_shows_checkout_and_bill_only_when_relevant(self):
        session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            payload=self.table.deep_link_payload,
            username=self.telegram_account.username,
            first_name="Nav",
        )
        cart = Cart.objects.create(
            partner=self.partner,
            guest=self.guest,
            table_session=session,
            status=Cart.Status.ACTIVE,
            total_amount="180.00",
        )
        CartItem.objects.create(
            partner=self.partner,
            cart=cart,
            menu_item=self.menu_item,
            item_name=self.menu_item.name,
            unit_price="180.00",
            quantity=1,
        )
        Order.objects.create(
            partner=self.partner,
            guest=self.guest,
            table=self.table,
            table_session=session,
            total_amount="180.00",
        )

        state = resolve_guest_navigation_state(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            content=self.content,
        )

        self.assertEqual(state.journey, GuestJourney.TABLE)
        self.assertTrue(state.has_active_session)
        self.assertTrue(state.has_active_cart)
        self.assertTrue(state.has_billable_activity)
        self.assertTrue(state.has_action(GuestAction.CHECKOUT))
        self.assertTrue(state.has_action(GuestAction.REQUEST_BILL))

    def test_resolver_hides_menu_and_cart_for_loyalty_only_config(self):
        self.content.module_menu_enabled = False
        self.content.module_tables_enabled = False
        self.content.module_cart_enabled = False
        self.content.module_orders_enabled = False

        state = resolve_guest_navigation_state(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            content=self.content,
        )

        self.assertFalse(state.has_action(GuestAction.MENU))
        self.assertFalse(state.has_action(GuestAction.CART))
        self.assertTrue(state.has_action(GuestAction.PROFILE))
