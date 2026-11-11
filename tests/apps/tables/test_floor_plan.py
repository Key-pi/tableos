"""The floor plan (PQ-052, step 1, owner 2026-11-11).

Halls with tables placed on a grid; the owner and managers arrange them in
Admin, everyone with the tables page sees the state of each table: free,
guests, bill asked for, staff called. A module of its own
(``module_floor_plan_enabled``)."""

from datetime import timedelta
from decimal import Decimal
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import BillingRequest
from apps.menu.models import MenuItem
from apps.notifications.models import GuestCall
from apps.orders.services import create_order_from_session
from apps.partners.models import PartnerBotSettings
from apps.tables.floor_plan import (
    FloorPlanError,
    floor_plan,
    save_hall_layout,
    table_states,
)
from apps.tables.models import Hall, Table
from apps.tables.services import VISIT_CHOICE_NEW, activate_table_session, close_table_visit
from apps.users.constants import AdminAccessPreset, AdminSection
from apps.users.models import User
from tests.apps.orders.test_order_admin_actions import admin_access
from tests.apps.users.test_admin_navigation import admin_user
from tests.webapp.test_guest_api import make_venue


class PlanFixture(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_floor_plan_enabled=True
        )
        self.hall = Hall.objects.create(partner=self.partner, name="Зал", width=12, height=8)
        self.t1 = Table.objects.create(partner=self.partner, number=1, seats=2)
        self.t2 = Table.objects.create(partner=self.partner, number=2, seats=4)
        self.t3 = Table.objects.create(partner=self.partner, number=3, seats=6)

    def place(self, table, x, y, w=2, h=2, shape="square"):
        return {"table": table.id.hex, "x": x, "y": y, "w": w, "h": h, "shape": shape}

    def scan(self, table, telegram_id, new=False):
        kwargs = {"visit_choice": VISIT_CHOICE_NEW} if new else {}
        return activate_table_session(
            partner_id=self.partner.id, telegram_id=telegram_id,
            payload=table.deep_link_payload, **kwargs,
        )


class LayoutTests(PlanFixture):
    def test_tables_are_placed_in_the_hall(self):
        save_hall_layout(partner_id=self.partner.id, hall_id=self.hall.id, placements=[
            self.place(self.t1, 0, 0, shape="round"), self.place(self.t2, 4, 0, w=3),
        ])
        self.t1.refresh_from_db()
        self.t2.refresh_from_db()
        self.assertEqual((self.t1.hall_id, self.t1.plan_x, self.t1.plan_y, self.t1.shape),
                         (self.hall.id, 0, 0, Table.Shape.ROUND))
        self.assertEqual((self.t2.plan_w, self.t2.plan_h), (3, 2))

    def test_the_hall_is_saved_whole(self):
        # A table left out of the hall's layout leaves the plan.
        save_hall_layout(partner_id=self.partner.id, hall_id=self.hall.id,
                         placements=[self.place(self.t1, 0, 0), self.place(self.t2, 4, 0)])
        save_hall_layout(partner_id=self.partner.id, hall_id=self.hall.id,
                         placements=[self.place(self.t2, 6, 2)])
        self.t1.refresh_from_db()
        self.assertEqual((self.t1.hall_id, self.t1.plan_x), (None, None))

    def test_bad_layouts_are_refused(self):
        cases = {
            "outside": [self.place(self.t1, 11, 0)],  # 11 + 2 > 12
            "overlap": [self.place(self.t1, 0, 0), self.place(self.t2, 1, 1)],
            "twice": [self.place(self.t1, 0, 0), self.place(self.t1, 4, 4)],
            "size": [self.place(self.t1, 0, 0, w=0)],
            "shape": [self.place(self.t1, 0, 0, shape="star")],
        }
        for name, placements in cases.items():
            with self.subTest(name), self.assertRaises(FloorPlanError):
                save_hall_layout(partner_id=self.partner.id, hall_id=self.hall.id,
                                 placements=placements)
        self.t1.refresh_from_db()
        self.assertIsNone(self.t1.hall_id)

    def test_only_this_venues_hall_and_tables(self):
        other = make_venue("Other")
        foreign_table = Table.objects.create(partner=other, number=1)
        foreign_hall = Hall.objects.create(partner=other, name="Чужий")
        with self.assertRaises(FloorPlanError):
            save_hall_layout(partner_id=self.partner.id, hall_id=self.hall.id,
                             placements=[self.place(foreign_table, 0, 0)])
        with self.assertRaises(FloorPlanError):
            save_hall_layout(partner_id=self.partner.id, hall_id=foreign_hall.id,
                             placements=[self.place(self.t1, 0, 0)])
        foreign_table.refresh_from_db()
        self.assertIsNone(foreign_table.hall_id)

    def test_a_hall_cannot_shrink_under_its_tables(self):
        save_hall_layout(partner_id=self.partner.id, hall_id=self.hall.id,
                         placements=[self.place(self.t1, 9, 0)])
        self.hall.width = 10
        with self.assertRaises(ValidationError):
            self.hall.full_clean()

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL only")
    def test_saves_of_one_hall_queue_up(self):
        # The hall row is locked first: two saves of one hall never mix.
        with CaptureQueriesContext(connection) as queries:
            save_hall_layout(partner_id=self.partner.id, hall_id=self.hall.id,
                             placements=[self.place(self.t1, 0, 0)])
        locks = [q["sql"] for q in queries.captured_queries if "FOR NO KEY UPDATE" in q["sql"]]
        self.assertTrue(locks and '"tables_hall"' in locks[0], locks)


class StateTests(PlanFixture):
    def test_free_guests_bill_and_call(self):
        self.assertEqual(table_states(self.partner.id)[self.t1.id]["state"], "free")
        session = self.scan(self.t1, 880001)
        self.scan(self.t1, 880002)
        state = table_states(self.partner.id)[self.t1.id]
        self.assertEqual((state["state"], state["guests"]), ("busy", 2))
        BillingRequest.objects.create(partner=self.partner, table=self.t1, guest=session.guest,
                                      table_session=session)
        self.assertEqual(table_states(self.partner.id)[self.t1.id]["state"], "bill")
        GuestCall.objects.create(partner=self.partner, table_session=session,
                                 guest=session.guest, table_number=1, call_target="waiter")
        self.assertEqual(table_states(self.partner.id)[self.t1.id]["state"], "call")
        self.assertEqual(table_states(self.partner.id)[self.t2.id]["state"], "free")

    def test_what_is_over_does_not_count(self):
        session = self.scan(self.t1, 880001)
        call = GuestCall.objects.create(partner=self.partner, table_session=session,
                                        guest=session.guest, table_number=1,
                                        call_target="waiter", taken_at=timezone.now())
        BillingRequest.objects.create(partner=self.partner, table=self.t1, guest=session.guest,
                                      table_session=session,
                                      status=BillingRequest.Status.PROCESSED)
        self.assertEqual(table_states(self.partner.id)[self.t1.id]["state"], "busy")
        GuestCall.objects.filter(id=call.id).update(taken_at=None)
        GuestCall.objects.filter(id=call.id).update(
            created_at=timezone.now() - timedelta(hours=4)
        )
        self.assertEqual(table_states(self.partner.id)[self.t1.id]["state"], "busy")
        close_table_visit(session.visit)
        self.assertEqual(table_states(self.partner.id)[self.t1.id]["state"], "free")

    def test_what_the_table_owes(self):
        session = self.scan(self.t1, 880001)
        latte = MenuItem.objects.get(partner=self.partner, name="Лате")
        create_order_from_session(partner_id=self.partner.id, table_session=session, items=[{
            "menu_item_id": latte.id, "item_name": "Лате", "unit_price": latte.price,
            "quantity": 2,
        }])
        self.assertEqual(table_states(self.partner.id)[self.t1.id]["to_pay"], Decimal("160.00"))

    def test_another_venue_is_not_mixed_in(self):
        other = make_venue("Other")
        Table.objects.create(partner=other, number=1)
        self.assertEqual(set(table_states(self.partner.id)), {self.t1.id, self.t2.id, self.t3.id})

    def test_the_plan_for_the_screens(self):
        save_hall_layout(partner_id=self.partner.id, hall_id=self.hall.id,
                         placements=[self.place(self.t1, 0, 0)])
        Hall.objects.create(partner=self.partner, name="Тераса", sort_order=2)
        plan = floor_plan(self.partner.id)
        self.assertEqual([hall["name"] for hall in plan["halls"]], ["Зал", "Тераса"])
        first = plan["halls"][0]["tables"][0]
        self.assertEqual((first["number"], first["x"], first["state"]), (1, 0, "free"))
        # Tables not on the plan are still shown — under it.
        self.assertEqual([table["number"] for table in plan["unplaced"]], [2, 3])


class ModuleTests(PlanFixture):
    def test_needs_the_tables_module(self):
        settings = PartnerBotSettings.objects.get(partner=self.partner)
        settings.module_tables_enabled = False
        settings.module_staff_call_enabled = False
        with self.assertRaises(ValidationError) as caught:
            settings.full_clean()
        self.assertIn("module_floor_plan_enabled", str(caught.exception))

    def test_off_by_default_and_changes_nothing_else(self):
        venue = make_venue("Fresh")
        settings = PartnerBotSettings.objects.get(partner=venue)
        self.assertFalse(settings.module_floor_plan_enabled)
        owner = admin_user(venue, "fresh_owner", AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse("admin:tables_hall_changelist")).status_code,
                         404)
        # The tables page works as before.
        self.assertEqual(self.client.get(reverse("admin:tables_table_changelist")).status_code,
                         200)


class AdminPlanTests(PlanFixture):
    def setUp(self):
        super().setUp()
        self.owner = admin_user(self.partner, "plan_owner", AdminAccessPreset.OWNER, owner=True)
        self.url = reverse("admin:tables_hall_changelist")

    def save(self, placements, hall=None):
        import json

        return self.client.post(
            reverse("admin:tables_hall_layout", args=[(hall or self.hall).pk]),
            data=json.dumps({"placements": placements}), content_type="application/json",
        )

    def test_the_owner_arranges_the_hall(self):
        self.client.force_login(self.owner)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Схема зала")
        self.assertTrue(page.context["can_edit"])
        response = self.save([self.place(self.t1, 2, 2, shape="round")])
        self.assertEqual(response.status_code, 200, response.content)
        self.t1.refresh_from_db()
        self.assertEqual((self.t1.plan_x, self.t1.shape), (2, Table.Shape.ROUND))
        # A bad layout comes back with a message, nothing saved.
        response = self.save([self.place(self.t1, 11, 0)])
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_the_state_refreshes_by_itself(self):
        self.client.force_login(self.owner)
        self.scan(self.t2, 880001)
        state = self.client.get(reverse("admin:tables_hall_state")).json()
        states = {table["number"]: table["state"]
                  for table in state["unplaced"]}
        self.assertEqual(states[2], "busy")

    def test_a_manager_arranges_too(self):
        manager = User.objects.create_user(username="plan_manager", password="x",
                                           partner=self.partner, role=User.Role.MANAGER)
        admin_access(manager, AdminAccessPreset.MANAGER)
        self.client.force_login(manager)
        self.assertEqual(self.save([self.place(self.t1, 0, 0)]).status_code, 200)

    def test_seeing_the_tables_is_not_arranging(self):
        viewer = User.objects.create_user(username="plan_viewer", password="x",
                                          partner=self.partner, role=User.Role.WAITER)
        admin_access(viewer, AdminAccessPreset.CUSTOM, view_only=[AdminSection.TABLES])
        self.client.force_login(viewer)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context["can_edit"])
        self.assertEqual(self.save([self.place(self.t1, 0, 0)]).status_code, 403)

    def test_another_venues_hall_is_not_found(self):
        other = make_venue("Other")
        foreign_hall = Hall.objects.create(partner=other, name="Чужий")
        self.client.force_login(self.owner)
        self.assertEqual(self.save([], hall=foreign_hall).status_code, 404)

    def test_in_the_menu_when_the_module_is_on(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("admin:index")), self.url)
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_floor_plan_enabled=False
        )
        self.assertNotContains(self.client.get(reverse("admin:index")), self.url)
