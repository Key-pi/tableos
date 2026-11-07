"""The catalog in the Mini App and the bot (owner, 2026-11-07)."""

from datetime import datetime, time
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse

from apps.menu.models import MenuBadge, MenuCategory, MenuItem, MenuItemAttribute
from apps.orders.kitchen import issue_screen_link
from apps.orders.models import KitchenScreen, Order, OrderItem
from apps.partners.models import PartnerBotSettings
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, GuestProfile, TelegramAccount, User
from bot.handlers.menu import _menu_tree, _pick, _render_menu_category_view
from bot.keyboards.menu import build_menu_keyboard
from bot.services.content import BotContent
from tests.webapp.test_guest_api import MiniAppFixture

KYIV = ZoneInfo("Europe/Kyiv")
MONDAY_7 = datetime(2026, 11, 9, 7, 0, tzinfo=KYIV)
MONDAY_9 = datetime(2026, 11, 9, 9, 0, tzinfo=KYIV)


def at(moment):
    return mock.patch("apps.menu.catalog.timezone.now", return_value=moment)


class CatalogFixture(MiniAppFixture):
    def setUp(self):
        super().setUp()
        self.clothes = MenuCategory.objects.create(partner=self.partner, name="Одяг", sort_order=5)
        self.shirts = MenuCategory.objects.create(
            partner=self.partner, name="Футболки", parent=self.clothes, sort_order=1
        )
        self.shirt = MenuItem.objects.create(
            partner=self.partner, category=self.shirts, name="Футболка", price="500.00",
            old_price="650.00", portion="1", portion_unit="pcs", badges=["new"], sku="TS-1",
        )
        self.shirt.custom_badges.add(MenuBadge.objects.create(partner=self.partner, name="Сезон"))
        MenuItemAttribute.objects.create(item=self.shirt, name="Матеріал", value="Бавовна")
        self.breakfast = MenuCategory.objects.create(
            partner=self.partner, name="Сніданки", sort_order=9,
            available_from=time(8), available_until=time(12),
        )
        self.omelette = MenuItem.objects.create(
            partner=self.partner, category=self.breakfast, name="Омлет", price="120.00",
        )

    def showcase(self, on=True):
        PartnerBotSettings.objects.filter(partner=self.partner).update(module_showcase_enabled=on)

    def menu(self):
        status, body = self.call("menu")
        self.assertEqual(status, 200, body)
        return body


class MiniAppCatalogTests(CatalogFixture):
    def test_two_levels_and_the_schedule(self):
        with at(MONDAY_7):
            categories = self.menu()["categories"]
        names = [(category["name"], category["parent"]) for category in categories]
        self.assertEqual(names, [
            ("Кава", None), ("Одяг", None), ("Футболки", self.clothes.id.hex), ("Сніданки", None),
        ])
        clothes = categories[1]
        self.assertEqual(clothes["items"], [])  # kept for its subcategory
        breakfast = categories[3]
        self.assertTrue(breakfast["closed"])
        self.assertEqual(breakfast["schedule"], "з 08:00 до 12:00")  # uk, the guest's language
        with at(MONDAY_9):
            self.assertFalse(self.menu()["categories"][3]["closed"])

    def test_the_showcase_only_while_its_module_is_on(self):
        shirt = self.menu()["categories"][2]["items"][0]
        for hidden in ("old_price", "badges", "portion", "attributes", "sku", "photos"):
            self.assertNotIn(hidden, shirt)
        self.showcase()
        shirt = self.menu()["categories"][2]["items"][0]
        self.assertEqual(shirt["old_price"], "650.00")
        self.assertEqual(shirt["portion"], "1 шт")
        self.assertEqual([badge["name"] for badge in shirt["badges"]], ["Новинка", "Сезон"])
        self.assertEqual(shirt["attributes"], [{"name": "Матеріал", "value": "Бавовна"}])
        self.assertEqual(shirt["sku"], "TS-1")

    def test_labels_in_the_guests_language(self):
        self.showcase()
        status, body = self.call("menu", language="en")
        shirt = body["categories"][2]["items"][0]
        self.assertEqual(shirt["badges"][0]["name"], "New")
        self.assertEqual(shirt["portion"], "1 pcs")


class BotCatalogTests(CatalogFixture):
    def tree(self, channel=None):
        return _menu_tree(self.partner, channel, showcase=False)

    def test_top_categories_then_the_subcategories_of_the_chosen_one(self):
        tree = self.tree()
        self.assertEqual(
            [branch["category"].name for branch in tree], ["Кава", "Одяг", "Сніданки"]
        )
        branch, chosen = _pick(tree, self.clothes.id)
        self.assertEqual(chosen["category"], self.shirts)  # no own items: its first sub
        keyboard = build_menu_keyboard(
            categories=[entry["category"] for entry in tree],
            active_category_id=branch["category"].id,
            subcategories=[entry["category"] for entry in branch["children"]],
            active_subcategory_id=chosen["category"].id,
            items=chosen["items"], has_session=True,
        )
        texts = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertIn("• Одяг", texts)
        self.assertIn("• Футболки", texts)
        self.assertIn("➕ Футболка — 500.00 грн", texts)

    def test_a_closed_category_is_shown_without_add_buttons(self):
        partner = self.partner
        text = _render_menu_category_view(
            partner=partner, content=BotContent.defaults(), category=self.breakfast,
            menu_items=[self.omelette], session=None, closed_when="з 08:00 до 12:00",
        )
        self.assertIn("Омлет", text)
        self.assertIn("08:00", text)

    def test_showcase_lines(self):
        self.showcase()
        tree = _menu_tree(self.partner, None, showcase=True)
        _branch, chosen = _pick(tree, self.shirts.id)
        text = _render_menu_category_view(
            partner=self.partner, content=BotContent.defaults(), category=self.shirts,
            menu_items=chosen["items"], session=None, showcase=True,
        )
        self.assertIn("🆕 Футболка", text)
        self.assertIn("<s>650.00 грн</s>", text)
        self.assertIn("Одяг › Футболки", text)


class KitchenPlaceTests(CatalogFixture):
    def test_a_subcategory_takes_its_parents_place(self):
        drinks = MenuCategory.objects.create(partner=self.partner, name="Напої", station="bar")
        cocktails = MenuCategory.objects.create(
            partner=self.partner, name="Коктейлі", parent=drinks
        )
        mojito = MenuItem.objects.create(
            partner=self.partner, category=cocktails, name="Мохіто", price="150.00"
        )
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_kitchen_screen_enabled=True
        )
        kitchen = KitchenScreen.objects.create(partner=self.partner, name="Кухня", shows="kitchen")
        bar = KitchenScreen.objects.create(partner=self.partner, name="Бар", shows="bar")
        kitchen_token, bar_token = issue_screen_link(kitchen), issue_screen_link(bar)
        guest = GuestProfile.objects.create(
            partner=self.partner, telegram_account=TelegramAccount.objects.create(telegram_id=1)
        )
        order = Order.objects.create(
            partner=self.partner, guest=guest, channel="pickup", status="accepted",
            total_amount=Decimal("150.00"),
        )
        OrderItem.objects.create(
            partner=self.partner, order=order, menu_item=mojito, item_name="Мохіто",
            unit_price=Decimal("150.00"), quantity=1,
        )

        def ids(token):
            response = self.client.get(reverse("webapp:kitchen_state"), HTTP_X_SCREEN_TOKEN=token)
            return [card["id"] for card in response.json()["orders"]]

        self.assertEqual(ids(bar_token), [order.public_id])
        self.assertEqual(ids(kitchen_token), [])


class ShowcaseAdminTests(TestCase):
    def setUp(self):
        from tests.webapp.test_guest_api import make_venue

        self.partner = make_venue("Shop")
        owner = User.objects.create_user(
            username="shop_owner", password="x", partner=self.partner, role=User.Role.OWNER,
            is_staff=True,
        )
        AdminAccessProfile.objects.create(
            user=owner, partner=self.partner, preset=AdminAccessPreset.OWNER,
            is_partner_owner=True, can_access_admin=True,
        )
        self.client.force_login(owner)
        self.item = MenuItem.objects.get(partner=self.partner, name="Лате")

    def page(self):
        return self.client.get(reverse("admin:menu_menuitem_change", args=[self.item.pk]))

    def test_the_showcase_part_follows_the_module(self):
        page = self.page()
        self.assertNotContains(page, 'name="old_price"')
        self.assertNotContains(page, "photos-TOTAL_FORMS")
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_showcase_enabled=True
        )
        page = self.page()
        self.assertContains(page, 'name="old_price"')
        self.assertContains(page, 'name="badges"')
        self.assertContains(page, "photos-TOTAL_FORMS")
        self.assertContains(page, "attributes-TOTAL_FORMS")

    def test_only_a_top_category_can_hold_others(self):
        top = MenuCategory.objects.create(partner=self.partner, name="Одяг")
        MenuCategory.objects.create(partner=self.partner, name="Футболки", parent=top)
        page = self.client.get(reverse("admin:menu_menucategory_add"))
        form = page.context["adminform"].form
        choices = [str(option) for option in form.fields["parent"].queryset]
        self.assertTrue(any("Одяг" in choice for choice in choices))
        self.assertFalse(any("Футболки" in choice for choice in choices))

    def test_the_schedule_is_saved_from_ticks(self):
        response = self.client.post(reverse("admin:menu_menucategory_add"), {
            "name": "Ланч", "sort_order": "100", "is_active": "on", "station": "",
            "available_from": "12:00", "available_until": "16:00",
            "available_weekdays": ["0", "1", "2", "3", "4"],
        })
        self.assertEqual(response.status_code, 302, getattr(response, "context", None)
                         and response.context["adminform"].form.errors)
        lunch = MenuCategory.objects.get(name="Ланч")
        self.assertEqual(lunch.available_weekdays, [0, 1, 2, 3, 4])


class ShowcaseModuleIndependenceTests(CatalogFixture):
    """PQ-033: the showcase works alone and changes nothing else."""

    def test_switching_it_changes_nothing_for_ordering(self):
        self.choose("pickup")
        self.add_latte()
        before = self.call("state")[1]["cart"]
        self.showcase()
        self.assertEqual(self.call("state")[1]["cart"], before)
        self.showcase(False)
        self.assertEqual(self.call("cart", {"action": "add", "item": self.shirt.public_id})[0], 200)
