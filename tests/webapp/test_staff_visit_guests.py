"""The people of a company on the staff screen (PQ-009, step 2).

The table card lists the guests — with the bot by name, the others «Гость 1»,
«Гость 2» — with what each has not got a bill for yet, and «Гостей за
столом»; the order screen asks «Для кого»: a guest at the table or a new one."""

from apps.menu.models import MenuItem
from apps.orders.models import Order
from apps.partners.models import PartnerBotSettings
from apps.tables.models import Table, TableVisit
from apps.users.models import User
from tests.webapp.test_staff_api import WAITER_TG, StaffFixture, staff


class VisitGuestsScreenTests(StaffFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_floor_plan_enabled=True, module_table_orders_enabled=True,
            module_billing_enabled=True,
        )
        self.table = Table.objects.create(partner=self.partner, number=3)
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")

    def order(self, key, **extra):
        status, body = self.staff_call("table_order", {
            "table": self.table.id.hex, "items": [{"item": self.latte.id.hex, "quantity": 1}],
            "key": key, **extra,
        }, user_id=WAITER_TG)
        self.assertEqual(status, 200, body)
        return Order.objects.get(request_key=key)

    def card(self):
        status, body = self.staff_call("tables", user_id=WAITER_TG)
        self.assertEqual(status, 200, body)
        return body["tables"][0]["visits"][0]

    def test_guests_without_the_bot_one_by_one(self):
        first = self.order("guests-key-01")
        visit = first.table_session.visit_id.hex
        second = self.order("guests-key-02", visit=visit, new_guest=True)
        self.assertNotEqual(first.guest_id, second.guest_id)
        card = self.card()
        self.assertEqual([(guest["label"], guest["bot"], guest["unbilled"])
                          for guest in card["guests"]],
                         [("Гість 1", False, "80.00"), ("Гість 2", False, "80.00")])
        self.assertEqual(card["headcount"], 2)
        again = self.order("guests-key-03", visit=visit, guest=card["guests"][1]["id"])
        self.assertEqual(again.guest_id, second.guest_id)
        self.assertEqual(self.card()["guests"][1]["unbilled"], "160.00")

    def test_how_many_sit_at_the_table(self):
        self.order("guests-key-04")
        visit = self.card()["id"]
        status, body = self.staff_call("visit_guests", {"visit": visit, "count": 4},
                                       user_id=WAITER_TG)
        self.assertEqual((status, body["headcount"]), (200, 4))
        self.assertEqual(self.card()["headcount"], 4)
        self.assertEqual(TableVisit.objects.get().guests_count, 4)
        self.assertEqual(self.staff_call("visit_guests", {"visit": visit, "count": 0},
                                         user_id=WAITER_TG)[0], 400)
        staff(self.partner, "kava_hookah_count", User.Role.HOOKAH_MASTER, 7700299)
        self.assertEqual(self.staff_call("visit_guests", {"visit": visit, "count": 3},
                                         user_id=7700299)[0], 403)

    def test_a_guest_who_is_not_at_the_table(self):
        self.order("guests-key-05")
        visit = self.card()["id"]
        status, body = self.staff_call("table_order", {
            "table": self.table.id.hex, "visit": visit, "guest": "0" * 32,
            "items": [{"item": self.latte.id.hex, "quantity": 1}], "key": "guests-key-06",
        }, user_id=WAITER_TG)
        self.assertEqual(status, 400, body)
