"""Stock in the guest's Mini App (catalog step 3, owner 2026-11-07): the
menu says «нет в наличии» or «осталось мало», never the number; the cart does
not take more than there is."""

from apps.menu.models import MenuItem, MenuItemVariant
from apps.menu.stock import LOW_STOCK
from tests.apps.menu.test_options import options_module
from tests.apps.menu.test_stock import stock_module
from tests.webapp.test_guest_api import MiniAppFixture


class StockMiniAppFixture(MiniAppFixture):
    def setUp(self):
        super().setUp()
        stock_module(self.partner)
        options_module(self.partner)
        self.shirt = MenuItem.objects.create(
            partner=self.partner, category=self.latte.category, name="Футболка",
            price="500.00", track_stock=True, available_for_pickup=True,
        )
        self.m = MenuItemVariant.objects.create(
            item=self.shirt, name="M", price="500.00", stock_quantity=LOW_STOCK, sort_order=1
        )
        self.l = MenuItemVariant.objects.create(
            item=self.shirt, name="L", price="500.00", stock_quantity=0, sort_order=2
        )
        self.cap = MenuItem.objects.create(
            partner=self.partner, category=self.latte.category, name="Кепка",
            price="300.00", track_stock=True, stock_quantity=0, available_for_pickup=True,
        )

    def item_json(self, item):
        status, body = self.call("menu")
        self.assertEqual(status, 200, body)
        return next(row for category in body["categories"] for row in category["items"]
                    if row["id"] == item.public_id)


class MenuStockTests(StockMiniAppFixture):
    def test_out_and_low_never_the_number(self):
        shirt = self.item_json(self.shirt)
        self.assertEqual(shirt["stock"], "low")
        self.assertEqual([(row["name"], row["stock"]) for row in shirt["variants"]],
                         [("M", "low"), ("L", "out")])
        self.assertNotIn("stock_quantity", shirt)
        self.assertEqual(self.item_json(self.cap)["stock"], "out")
        self.assertNotIn("stock", self.item_json(self.latte))  # not counted

    def test_plenty_says_nothing(self):
        self.m.stock_quantity = LOW_STOCK + 1
        self.m.save()
        self.assertEqual(self.item_json(self.shirt)["stock"], "")

    def test_nothing_while_the_module_is_off(self):
        stock_module(self.partner, on=False)
        shirt = self.item_json(self.shirt)
        self.assertNotIn("stock", shirt)
        self.assertTrue(all("stock" not in row for row in shirt["variants"]))


class CartStockTests(StockMiniAppFixture):
    def test_the_cart_says_it_is_out(self):
        self.choose("pickup")
        status, body = self.call("cart", {"action": "add", "item": self.cap.public_id})
        self.assertEqual(status, 400, body)
        self.assertIn("Кепка", body["error"])
        status, body = self.call(
            "cart", {"action": "add", "item": self.shirt.public_id, "variant": self.l.id.hex}
        )
        self.assertEqual(status, 400, body)
