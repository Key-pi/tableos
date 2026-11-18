"""«Рассчитать» on the staff screen (PQ-009, step 3).

The table card opens the settlement editor: the company's lines, where each
is (a guest or «Общее»); «Готово» makes the bills; each guest then pays with
one tap. The service decides (``apps/billing/visit_settlement.py``)."""

from apps.billing.models import Bill, Payment
from apps.menu.models import MenuItem
from apps.orders.models import Order
from apps.partners.models import PartnerBotSettings
from apps.tables.models import Table
from tests.webapp.test_staff_api import OWNER_TG, WAITER_TG, StaffFixture


class SettlementScreenTests(StaffFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_floor_plan_enabled=True, module_table_orders_enabled=True,
            module_billing_enabled=True,
        )
        self.table = Table.objects.create(partner=self.partner, number=3)
        latte = MenuItem.objects.get(partner=self.partner, name="Лате")
        hookah = MenuItem.objects.get(partner=self.partner, name="Кальян")
        self.first = self.order("settle-key-01", latte)
        self.visit = self.first.table_session.visit_id.hex
        self.second = self.order("settle-key-02", hookah, visit=self.visit, new_guest=True)

    def order(self, key, item, **extra):
        status, body = self.staff_call("table_order", {
            "table": self.table.id.hex, "items": [{"item": item.id.hex, "quantity": 1}],
            "key": key, **extra,
        }, user_id=WAITER_TG)
        self.assertEqual(status, 200, body)
        return Order.objects.get(request_key=key)

    def settlement(self, user_id=OWNER_TG):
        status, body = self.staff_call("settlement", user_id=user_id,
                                       query=f"?visit={self.visit}")
        return status, body.get("settlement")

    def test_the_table_card_offers_it(self):
        visit = self.staff_call("tables")[1]["tables"][0]["visits"][0]
        self.assertTrue(visit["can_settle"])

    def test_lay_out_and_pay_guest_by_guest(self):
        status, settlement = self.settlement()
        self.assertEqual(status, 200)
        self.assertFalse(settlement["laid_out"])
        first, second = settlement["guests"]
        latte, hookah = settlement["lines"]
        self.assertEqual((latte["place"], hookah["place"]), (first["id"], second["id"]))
        status, body = self.staff_call("settlement_save", {
            "visit": self.visit, "places": {latte["id"]: first["id"], hookah["id"]: "shared"},
            "sharers": [first["id"], second["id"]],
        })
        self.assertEqual(status, 200, body)
        settlement = body["settlement"]
        self.assertTrue(settlement["laid_out"])
        self.assertEqual([guest["to_pay"] for guest in settlement["guests"]],
                         ["280.00", "200.00"])  # 80 + 400 / 2, 400 / 2
        status, body = self.staff_call("settlement_pay", {
            "visit": self.visit, "guest": first["id"], "method": "cash", "amount": "280.00",
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(body["settlement"]["guests"][0]["to_pay"], "0.00")
        self.assertEqual(self.staff_call("settlement_pay", {
            "visit": self.visit, "guest": second["id"], "method": "terminal", "amount": "999.00",
        })[0], 400)  # stale amount
        self.staff_call("settlement_pay", {
            "visit": self.visit, "guest": second["id"], "method": "terminal", "amount": "200.00",
        })
        self.assertEqual(set(Bill.objects.values_list("status", flat=True)), {Bill.Status.PAID})
        self.assertEqual(Payment.objects.count(), 3)

    def test_a_waiter_does_not_settle(self):
        self.assertEqual(self.settlement(user_id=WAITER_TG)[0], 403)
        self.assertEqual(self.staff_call("settlement_save", {
            "visit": self.visit, "places": {}, "sharers": [],
        }, user_id=WAITER_TG)[0], 403)

    def test_bad_requests(self):
        self.assertEqual(self.staff_call("settlement_save", {
            "visit": self.visit, "places": [], "sharers": [],
        })[0], 400)
        self.assertEqual(self.staff_call("settlement_save", {
            "visit": self.visit, "places": {"x": "y"}, "sharers": [],
        })[0], 400)
        self.assertEqual(self.staff_call("settlement_pay", {
            "visit": self.visit, "guest": "0" * 32, "method": "bonuses",
        })[0], 400)
