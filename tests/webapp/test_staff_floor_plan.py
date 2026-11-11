"""The floor plan on the staff screen's «Столы» tab (PQ-052, step 1).

With the module on, the tab gets the halls and every table with its state;
a table with guests keeps its visit card. Without it — as before."""

from apps.partners.models import PartnerBotSettings
from apps.tables.floor_plan import save_hall_layout
from apps.tables.models import Hall, Table
from tests.webapp.test_staff_api import BillsFixture


class StaffPlanTests(BillsFixture):
    def test_the_plan_with_states(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_floor_plan_enabled=True
        )
        hall = Hall.objects.create(partner=self.partner, name="Зал", width=10, height=6)
        free = Table.objects.create(partner=self.partner, number=8)
        save_hall_layout(partner_id=self.partner.id, hall_id=hall.id, placements=[
            {"table": self.table.id.hex, "x": 0, "y": 0, "w": 2, "h": 2, "shape": "round"},
            {"table": free.id.hex, "x": 4, "y": 0, "w": 2, "h": 2, "shape": "square"},
        ])
        status, body = self.staff_call("tables", user_id=7700204)
        self.assertEqual(status, 200, body)
        tables = {table["number"]: table for table in body["plan"]["halls"][0]["tables"]}
        self.assertEqual((tables[7]["state"], tables[7]["shape"]), ("busy", "round"))
        self.assertEqual(tables[8]["state"], "free")
        # The busy table's card is found by its id.
        card = next(table for table in body["tables"] if table["number"] == 7)
        self.assertEqual(card["id"], tables[7]["id"])

    def test_without_the_module_as_before(self):
        status, body = self.staff_call("tables", user_id=7700204)
        self.assertEqual(status, 200)
        self.assertNotIn("plan", body)
