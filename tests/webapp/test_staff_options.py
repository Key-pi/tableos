"""A variant and add-ons in the staff screen's quick sale (catalog step 2,
owner 2026-11-07)."""

from decimal import Decimal

from apps.bonuses.models import WalkInSale
from apps.menu.models import MenuItemVariant, Modifier, ModifierGroup
from apps.partners.models import PartnerBotSettings
from apps.users.models import User
from tests.apps.menu.test_options import options_module
from tests.webapp.test_staff_api import StaffFixture, staff

CASHIER_TG = 7700214


class StaffSaleOptionsTests(StaffFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_quick_sale_enabled=True
        )
        options_module(self.partner)
        staff(self.partner, "kava_cashier2", User.Role.CASHIER, CASHIER_TG)
        self.big = MenuItemVariant.objects.create(item=self.latte, name="400 мл", price="95.00")
        milk = ModifierGroup.objects.create(
            partner=self.partner, name="Молоко", min_choices=1, max_choices=1
        )
        self.oat = Modifier.objects.create(group=milk, name="Вівсяне", price="15.00")
        milk.items.add(self.latte)

    def sale(self, name, body):
        return self.staff_call(name, body, user_id=CASHIER_TG)

    def test_the_menu_and_a_sale_with_a_choice(self):
        menu = self.sale("sale_menu", None)[1]
        latte = next(item for item in menu["categories"][0]["items"] if item["name"] == "Лате")
        self.assertEqual((latte["choice"], latte["price"]), ("required", "95.00"))
        self.assertEqual(latte["variants"][0]["name"], "400 мл")
        body = {"customer_code": "", "items": [{
            "item": latte["id"], "quantity": 2, "variant": self.big.id.hex,
            "options": [self.oat.id.hex],
        }]}
        status, preview = self.sale("sale_preview", body)
        self.assertEqual(status, 200, preview)
        line = preview["sale"]["items"][0]
        self.assertEqual((line["name"], line["total"]), ("Лате, 400 мл (Вівсяне)", "220.00"))
        self.assertTrue(line["key"])
        status, done = self.sale("sale_confirm", body)
        self.assertEqual(status, 200, done)
        self.assertEqual(WalkInSale.objects.get().amount, Decimal("220.00"))

    def test_without_the_choice_there_is_no_sale(self):
        menu = self.sale("sale_menu", None)[1]
        latte = next(item for item in menu["categories"][0]["items"] if item["name"] == "Лате")
        status, body = self.sale("sale_confirm", {"items": [{"item": latte["id"], "quantity": 1}]})
        self.assertEqual(status, 400)
        self.assertIn("Лате", body["error"])
        for items in ([{"item": latte["id"], "quantity": 1, "options": "oat"}], ["latte"]):
            self.assertEqual(self.sale("sale_preview", {"items": items})[0], 400, items)
        self.assertFalse(WalkInSale.objects.exists())

    def test_a_screen_opened_before_still_sends_items_by_id(self):
        options_module(self.partner, on=False)
        menu = self.sale("sale_menu", None)[1]
        latte = next(item for item in menu["categories"][0]["items"] if item["name"] == "Лате")
        status, preview = self.sale("sale_preview", {"items": {latte["id"]: 2}})
        self.assertEqual((status, preview["sale"]["total"]), (200, "160.00"))
