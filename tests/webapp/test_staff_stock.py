"""The staff screen's quick sale shows how many are left (catalog step 3,
owner 2026-11-07)."""

from apps.menu.models import MenuItemVariant
from apps.orders.models import Order
from apps.partners.models import PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import User
from tests.apps.menu.test_options import options_module
from tests.apps.menu.test_stock import stock_module
from tests.webapp.test_guest_api import TG
from tests.webapp.test_staff_api import StaffFixture, staff

CASHIER_TG = 7700215


class StaffSaleStockTests(StaffFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_quick_sale_enabled=True
        )
        stock_module(self.partner)
        options_module(self.partner)
        staff(self.partner, "kava_cashier3", User.Role.CASHIER, CASHIER_TG)
        self.latte.track_stock = True
        self.latte.stock_quantity = 7
        self.latte.save()

    def latte_json(self):
        status, menu = self.staff_call("sale_menu", None, user_id=CASHIER_TG)
        self.assertEqual(status, 200, menu)
        return next(item for category in menu["categories"] for item in category["items"]
                    if item["name"] == "Лате")

    def test_what_is_left(self):
        self.assertEqual(self.latte_json()["left"], 7)
        MenuItemVariant.objects.create(item=self.latte, name="400 мл", price="95.00",
                                       stock_quantity=-1)
        latte = self.latte_json()
        self.assertNotIn("left", latte)  # counted by variant
        self.assertEqual(latte["variants"][0]["left"], -1)

    def test_not_counted_says_nothing(self):
        stock_module(self.partner, on=False)
        self.assertNotIn("left", self.latte_json())


class StaffRefundStockTests(StaffFixture):
    """«Вернуть на склад» in the staff screen's refund (catalog step 3)."""

    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_billing_enabled=True
        )
        stock_module(self.partner)
        self.latte.track_stock = True
        self.latte.stock_quantity = 3
        self.latte.save()
        staff(self.partner, "kava_cashier4", User.Role.CASHIER, CASHIER_TG)
        table = Table.objects.create(partner=self.partner, number=7)
        activate_table_session(
            partner_id=self.partner.id, telegram_id=TG, payload=table.deep_link_payload
        )
        self.add_latte()
        self.assertEqual(self.call("place", {})[0], 200)
        visit = self.staff_call("tables", user_id=CASHIER_TG)[1]["tables"][0]["visits"][0]
        self.staff_call("visit_action", {"visit": visit["id"], "action": "shared"},
                        user_id=CASHIER_TG)
        bill_id = self.staff_call("bills", user_id=CASHIER_TG)[1]["bills"][0]["id"]
        self.staff_call("bill_action", {"bill": bill_id, "action": "pay_cash"},
                        user_id=CASHIER_TG)
        self.order = Order.objects.get(table=table)
        self.lines = [line["id"] for line in self.staff_call(
            "refund", query=f"?order={self.order.public_id}"
        )[1]["lines"]]

    def stock(self):
        self.latte.refresh_from_db()
        return self.latte.stock_quantity

    def test_the_preview_offers_the_tick_and_it_puts_back(self):
        self.assertEqual(self.stock(), 2)
        preview = self.staff_call(
            "refund_preview", {"order": self.order.public_id, "lines": self.lines}
        )[1]
        self.assertTrue(preview["can_restock"])
        status, body = self.staff_call("refund_do", {
            "order": self.order.public_id, "lines": self.lines, "reason": "quality",
            "method": "cash", "restock": True,
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(self.stock(), 3)

    def test_without_the_tick_it_stays_out(self):
        status, body = self.staff_call("refund_do", {
            "order": self.order.public_id, "lines": self.lines, "reason": "quality",
            "method": "cash",
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(self.stock(), 2)
