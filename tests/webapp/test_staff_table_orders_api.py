"""The tablet's «Новый заказ» on the staff screen (PQ-052, step 2).

A waiter gets the «Столы» tab to order, but not the bills or closing a
visit; the menu is what a guest could order at a table now; a retry with
the same key makes one order."""

from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Order
from apps.partners.models import PartnerBotSettings
from apps.tables.models import Table, TableVisit
from apps.users.models import User
from tests.webapp.test_staff_api import WAITER_TG, StaffFixture, staff


class TabletOrderTests(StaffFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_floor_plan_enabled=True, module_table_orders_enabled=True,
            module_billing_enabled=True,
        )
        self.table = Table.objects.create(partner=self.partner, number=3)
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")

    def test_a_waiter_orders_but_does_not_settle(self):
        rights = self.staff_call("state", user_id=WAITER_TG)[1]["rights"]
        self.assertEqual((rights["tables"], rights["table_orders"]), (False, True))
        status, body = self.staff_call("tables", user_id=WAITER_TG)
        self.assertEqual(status, 200, body)
        self.assertIn("plan", body)
        status, body = self.staff_call("table_order", {
            "table": self.table.id.hex, "items": [{"item": self.latte.id.hex, "quantity": 1}],
            "key": "tablet-key-01",
        }, user_id=WAITER_TG)
        self.assertEqual(status, 200, body)
        self.assertIn("3", body["notice"])
        order = Order.objects.get()
        self.assertEqual((order.status, order.table_id), ("accepted", self.table.id))
        visit = TableVisit.objects.get(table=self.table)
        status, body = self.staff_call("visit_action", {"visit": visit.id.hex, "action": "close"},
                                       user_id=WAITER_TG)
        self.assertEqual(status, 403)

    def test_the_menu_is_the_tables_menu(self):
        shop = MenuCategory.objects.create(partner=self.partner, name="Навинос")
        MenuItem.objects.create(partner=self.partner, category=shop, name="Зерно",
                                price="300.00", available_at_table=False)
        status, body = self.staff_call("table_order_menu", user_id=WAITER_TG)
        self.assertEqual(status, 200, body)
        names = [item["name"] for category in body["categories"] for item in category["items"]]
        self.assertIn("Кальян", names)  # table only — but this is a table
        self.assertNotIn("Зерно", names)
        # A category out of its hours is not offered.
        from datetime import timedelta

        from apps.menu.catalog import local_now

        later = (local_now(self.partner) + timedelta(hours=3)).time().replace(second=0,
                                                                              microsecond=0)
        until = (local_now(self.partner) + timedelta(hours=3, minutes=5)).time().replace(
            second=0, microsecond=0)
        night = MenuCategory.objects.create(partner=self.partner, name="Ніч",
                                            available_from=later, available_until=until)
        MenuItem.objects.create(partner=self.partner, category=night, name="Нічний",
                                price="10.00")
        names = [item["name"] for category in self.staff_call(
            "table_order_menu", user_id=WAITER_TG)[1]["categories"] for item in category["items"]]
        self.assertNotIn("Нічний", names)

    def test_the_preview_and_one_order_per_key(self):
        status, body = self.staff_call("table_order_preview", {
            "items": [{"item": self.latte.id.hex, "quantity": 2}],
        }, user_id=WAITER_TG)
        self.assertEqual((status, body["order"]["total"]), (200, "160.00"))
        payload = {"table": self.table.id.hex, "key": "tablet-key-02",
                   "items": [{"item": self.latte.id.hex, "quantity": 2}]}
        self.assertEqual(self.staff_call("table_order", payload, user_id=WAITER_TG)[0], 200)
        visit = TableVisit.objects.get(table=self.table)
        status, body = self.staff_call("table_order", {**payload, "visit": visit.id.hex},
                                       user_id=WAITER_TG)
        self.assertEqual(status, 200, body)
        self.assertEqual(Order.objects.count(), 1)

    def test_a_hookah_master_has_no_tablet_orders(self):
        staff(self.partner, "kava_hookah", User.Role.HOOKAH_MASTER, 7700299)
        rights = self.staff_call("state", user_id=7700299)[1]["rights"]
        self.assertFalse(rights["table_orders"])
        status, body = self.staff_call("table_order", {
            "table": self.table.id.hex, "items": [{"item": self.latte.id.hex, "quantity": 1}],
        }, user_id=7700299)
        self.assertEqual(status, 403)

    def test_without_the_module(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_table_orders_enabled=False
        )
        self.assertFalse(self.staff_call("state", user_id=WAITER_TG)[1]["rights"]["table_orders"])
        self.assertEqual(self.staff_call("table_order_menu", user_id=WAITER_TG)[0], 403)
