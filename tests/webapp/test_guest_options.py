"""Variants and add-ons in the guest's Mini App (catalog step 2, owner
2026-11-07): the menu says what can be chosen, the cart keeps a line per
choice, the order gets it."""

from decimal import Decimal

from apps.menu.models import MenuItemVariant, Modifier, ModifierGroup
from apps.orders.models import CartItem, Order, OrderItem
from tests.apps.menu.test_options import options_module
from tests.webapp.test_guest_api import MiniAppFixture


class OptionsMiniAppFixture(MiniAppFixture):
    def setUp(self):
        super().setUp()
        options_module(self.partner)
        self.small = MenuItemVariant.objects.create(
            item=self.latte, name="250 мл", price="80.00", sort_order=1
        )
        self.big = MenuItemVariant.objects.create(
            item=self.latte, name="400 мл", price="95.00", sort_order=2
        )
        self.milk = ModifierGroup.objects.create(
            partner=self.partner, name="Молоко", min_choices=1, max_choices=1, sort_order=1
        )
        self.cow = Modifier.objects.create(group=self.milk, name="Коров'яче", sort_order=1)
        self.oat = Modifier.objects.create(
            group=self.milk, name="Вівсяне", price="15.00", sort_order=2
        )
        self.milk.categories.add(self.latte.category)

    def latte_json(self):
        status, body = self.call("menu")
        self.assertEqual(status, 200, body)
        return next(item for category in body["categories"] for item in category["items"]
                    if item["id"] == self.latte.public_id)

    def add(self, variant=None, modifiers=()):
        data = {"action": "add", "item": self.latte.public_id,
                "options": [modifier.id.hex for modifier in modifiers]}
        if variant is not None:
            data["variant"] = variant.id.hex
        return self.call("cart", data)


class MenuTests(OptionsMiniAppFixture):
    def test_the_menu_says_what_can_be_chosen(self):
        latte = self.latte_json()
        self.assertEqual((latte["choice"], latte["price"], latte["price_from"]),
                         ("required", "80.00", True))
        self.assertEqual([row["name"] for row in latte["variants"]], ["250 мл", "400 мл"])
        milk = latte["groups"][0]
        self.assertEqual((milk["name"], milk["min"], milk["max"]), ("Молоко", 1, 1))
        self.assertEqual([(row["name"], row["price"]) for row in milk["options"]],
                         [("Коров'яче", "0.00"), ("Вівсяне", "15.00")])

    def test_nothing_to_choose_while_the_module_is_off(self):
        options_module(self.partner, on=False)
        latte = self.latte_json()
        self.assertEqual((latte["choice"], latte["price"], latte["price_from"]),
                         ("none", "80.00", False))
        self.assertNotIn("variants", latte)
        self.assertNotIn("groups", latte)


class CartTests(OptionsMiniAppFixture):
    def setUp(self):
        super().setUp()
        self.choose("pickup")

    def test_a_line_per_choice(self):
        self.add(self.big, [self.oat])
        status, body = self.add(self.small, [self.cow])
        self.assertEqual(status, 200, body)
        lines = body["cart"]["items"]
        self.assertEqual([(line["name"], line["unit_price"]) for line in lines], [
            ("Лате, 400 мл (Вівсяне)", "110.00"), ("Лате, 250 мл (Коров'яче)", "80.00"),
        ])
        self.assertEqual(body["cart"]["total"], "190.00")
        self.assertEqual(lines[0]["line"], CartItem.objects.get(variant=self.big).id.hex)

    def test_without_the_choice_the_guest_is_told_what_to_pick(self):
        status, body = self.add()
        self.assertEqual(status, 400)
        self.assertIn("Лате", body["error"])
        status, body = self.add(self.big)
        self.assertEqual(status, 400)
        self.assertIn("Молоко", body["error"])

    def test_lines_change_and_go_by_their_own_id(self):
        self.add(self.big, [self.oat])
        cart = self.add(self.small, [self.cow])[1]["cart"]
        big, small = cart["items"]
        cart = self.call("cart", {"action": "change", "line": big["line"], "delta": 1})[1]["cart"]
        self.assertEqual([line["quantity"] for line in cart["items"]], [2, 1])
        cart = self.call("cart", {"action": "remove", "line": small["line"]})[1]["cart"]
        self.assertEqual([line["name"] for line in cart["items"]], ["Лате, 400 мл (Вівсяне)"])

    def test_odd_input_is_refused(self):
        for data in (
            {"action": "add", "item": self.latte.public_id, "variant": 5},
            {"action": "add", "item": self.latte.public_id, "options": "oat"},
            {"action": "add", "item": self.latte.public_id, "options": ["x"] * 60},
            {"action": "add", "item": self.latte.public_id, "variant": self.big.id.hex,
             "options": ["not-a-uuid"]},
            {"action": "change", "line": "nonsense", "delta": 1},
        ):
            self.assertEqual(self.call("cart", data)[0], 400, data)
        self.assertFalse(CartItem.objects.exists())

    def test_the_order_gets_the_choice(self):
        self.add(self.big, [self.oat])
        self.call("details", {"phone": "+380501112233"})
        quote = self.call("checkout")[1]
        self.assertEqual(quote["to_pay"], "110.00")
        status, body = self.call("place", {
            "slot": "now", "payment": "prepaid",
            "expected_total": quote["total"], "expected_to_pay": quote["to_pay"],
        })
        self.assertEqual(status, 200, body)
        line = OrderItem.objects.get(order__public_id=body["order"])
        self.assertEqual((line.item_name, line.unit_price, line.variant),
                         ("Лате, 400 мл (Вівсяне)", Decimal("110.00"), self.big))

    def test_an_add_on_taken_off_sale_stops_the_checkout(self):
        self.add(self.big, [self.oat])
        self.call("details", {"phone": "+380501112233"})
        self.oat.is_available = False
        self.oat.save()
        status, body = self.call("checkout")
        self.assertEqual(status, 400)
        self.assertIn("Вівсяне", body["error"])
        self.assertFalse(Order.objects.exists())
