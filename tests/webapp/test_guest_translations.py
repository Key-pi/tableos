"""The Mini App in the guest's language (catalog step 4, owner 2026-11-07):
the menu, the choice and the cart use the owner's translations where there
are some and the original where there are none."""

from apps.menu.models import (
    MenuBadge,
    MenuCategory,
    MenuItemAttribute,
    MenuItemVariant,
    Modifier,
    ModifierGroup,
)
from apps.partners.models import PartnerBotSettings
from tests.apps.menu.test_options import options_module
from tests.webapp.test_guest_api import MiniAppFixture


class GuestTranslationsTests(MiniAppFixture):
    def setUp(self):
        super().setUp()
        options_module(self.partner)
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_showcase_enabled=True
        )
        self.category = MenuCategory.objects.get(pk=self.latte.category_id)
        self.category.translations = {"en": {"name": "Coffee"}}
        self.category.save()
        self.latte.translations = {"en": {"name": "Latte", "description": "Soft coffee"}}
        self.latte.description = "Ніжна кава"
        self.latte.save()
        self.big = MenuItemVariant.objects.create(
            item=self.latte, name="400 мл", price="95.00", translations={"en": {"name": "400 ml"}}
        )
        group = ModifierGroup.objects.create(
            partner=self.partner, name="Молоко", translations={"en": {"name": "Milk"}}
        )
        group.items.add(self.latte)
        self.oat = Modifier.objects.create(group=group, name="Вівсяне", price="10.00")
        MenuItemAttribute.objects.create(
            item=self.latte, name="Обсмаження", value="середнє",
            translations={"en": {"name": "Roast"}},
        )
        badge = MenuBadge.objects.create(
            partner=self.partner, name="Сезонне", translations={"en": {"name": "Seasonal"}}
        )
        self.latte.custom_badges.add(badge)

    def menu(self, language):
        status, body = self.call("menu", language=language)
        self.assertEqual(status, 200, body)
        category = next(row for row in body["categories"] if row["id"] == self.category.id.hex)
        item = next(row for row in category["items"] if row["id"] == self.latte.public_id)
        return category, item

    def test_the_menu_in_english(self):
        category, item = self.menu("en")
        self.assertEqual((category["name"], item["name"], item["description"]),
                         ("Coffee", "Latte", "Soft coffee"))
        self.assertEqual([row["name"] for row in item["variants"]], ["400 ml"])
        self.assertEqual(item["groups"][0]["name"], "Milk")
        self.assertEqual(item["groups"][0]["options"][0]["name"], "Вівсяне")  # not translated
        self.assertEqual(item["attributes"], [{"name": "Roast", "value": "середнє"}])
        self.assertEqual(item["badges"][-1]["name"], "Seasonal")

    def test_the_original_in_the_main_language(self):
        category, item = self.menu("uk")
        self.assertEqual((category["name"], item["name"]), (self.category.name, "Лате"))
        self.assertEqual(item["groups"][0]["name"], "Молоко")

    def test_the_cart_line_in_english(self):
        self.oat.translations = {"en": {"name": "Oat"}}
        self.oat.save()
        self.call("channel", {"channel": "pickup"}, language="en")
        status, body = self.call("cart", {
            "action": "add", "item": self.latte.public_id, "variant": self.big.id.hex,
            "options": [self.oat.id.hex],
        }, language="en")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["cart"]["items"][0]["name"], "Latte, 400 ml (Oat)")
        status, body = self.call("state", language="uk")
        self.assertEqual(body["cart"]["items"][0]["name"], "Лате, 400 мл (Вівсяне)")
