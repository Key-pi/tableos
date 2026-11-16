"""Reservations on the staff screen (PQ-052, step 3).

The «Столы» tab books tables: a list for a day, a form, «Пришли», «Не
пришли», «Отменить». Times are the venue's local times."""

from datetime import timedelta

from apps.menu.catalog import local_now
from apps.partners.models import PartnerBotSettings
from apps.tables.models import Reservation, Table, TableVisit
from apps.users.models import User
from tests.webapp.test_staff_api import WAITER_TG, StaffFixture, staff


class StaffReservationTests(StaffFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_floor_plan_enabled=True, module_reservations_enabled=True,
        )
        self.table = Table.objects.create(partner=self.partner, number=9, seats=6)
        self.later = local_now(self.partner) + timedelta(hours=3)

    def form(self, **extra):
        return {
            "table": self.table.id.hex, "date": self.later.strftime("%Y-%m-%d"),
            "time": self.later.strftime("%H:%M"), "minutes": 120, "guests": 5,
            "name": "Олена", "phone": "+380671234567", "comment": "біля вікна", **extra,
        }

    def test_a_waiter_books_and_seats(self):
        rights = self.staff_call("state", user_id=WAITER_TG)[1]["rights"]
        self.assertTrue(rights["reservations"])
        status, body = self.staff_call("reservation_save", self.form(), user_id=WAITER_TG)
        self.assertEqual(status, 200, body)
        booking = Reservation.objects.get()
        self.assertEqual(
            booking.starts_at.astimezone(self.later.tzinfo).strftime("%H:%M"),
            self.later.strftime("%H:%M"),
        )
        status, body = self.staff_call(
            "reservations", query=f"?date={self.later:%Y-%m-%d}", user_id=WAITER_TG)
        self.assertEqual(status, 200, body)
        self.assertEqual([item["name"] for item in body["reservations"]], ["Олена"])
        self.assertEqual(body["reservations"][0]["table"], 9)
        status, body = self.staff_call("reservation_save", self.form(name="Друга"),
                                       user_id=WAITER_TG)
        self.assertEqual(status, 400)  # the same table and time
        status, body = self.staff_call(
            "reservation_action", {"id": booking.id.hex, "action": "seat"}, user_id=WAITER_TG)
        self.assertEqual(status, 200, body)
        self.assertTrue(TableVisit.objects.filter(table=self.table, status="active").exists())

    def test_change_and_cancel(self):
        self.staff_call("reservation_save", self.form(), user_id=WAITER_TG)
        booking = Reservation.objects.get()
        status, body = self.staff_call("reservation_save",
                                       self.form(id=booking.id.hex, guests=2), user_id=WAITER_TG)
        self.assertEqual(status, 200, body)
        booking.refresh_from_db()
        self.assertEqual(booking.guests, 2)
        self.staff_call("reservation_action", {"id": booking.id.hex, "action": "cancel"},
                        user_id=WAITER_TG)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Reservation.Status.CANCELED)

    def test_the_plan_knows_the_booking(self):
        soon = local_now(self.partner) + timedelta(minutes=40)
        self.staff_call("reservation_save", self.form(date=f"{soon:%Y-%m-%d}",
                                                      time=f"{soon:%H:%M}"), user_id=WAITER_TG)
        body = self.staff_call("tables", user_id=WAITER_TG)[1]
        entry = next(table for table in body["plan"]["unplaced"] if table["number"] == 9)
        self.assertEqual(entry["state"], "reserved")
        self.assertEqual(entry["reservation"]["time"], f"{soon:%H:%M}")

    def test_not_for_a_hookah_master_nor_without_the_module(self):
        staff(self.partner, "kava_hookah", User.Role.HOOKAH_MASTER, 7700297)
        self.assertFalse(self.staff_call("state", user_id=7700297)[1]["rights"]["reservations"])
        self.assertEqual(self.staff_call("reservation_save", self.form(), user_id=7700297)[0],
                         403)
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_reservations_enabled=False
        )
        self.assertFalse(self.staff_call("state", user_id=WAITER_TG)[1]["rights"]["reservations"])
        self.assertEqual(self.staff_call("reservations", user_id=WAITER_TG)[0], 403)

    def test_booked_for_a_guest_by_code(self):
        """PQ-056: the code instead of the name and phone; the list shows it."""

        from unittest.mock import patch

        from tests.apps.tables.test_reservation_guest import Sent, make_guest

        guest = make_guest(self.partner)
        with patch("apps.notifications.services._send_telegram_messages", new=Sent()):
            status, body = self.staff_call(
                "reservation_save",
                self.form(name="", phone="", customer_code=guest.customer_code.lower()),
                user_id=WAITER_TG,
            )
        self.assertEqual(status, 200, body)
        self.assertEqual((body["reservation"]["name"], body["reservation"]["customer_code"]),
                         ("Олена Коваль", guest.customer_code))
        status, body = self.staff_call(
            "reservations", query=f"?date={self.later:%Y-%m-%d}", user_id=WAITER_TG)
        self.assertEqual(body["reservations"][0]["customer_code"], guest.customer_code)
        status, body = self.staff_call("reservation_save",
                                       self.form(customer_code="NOPE00", time="23:59"),
                                       user_id=WAITER_TG)
        self.assertEqual(status, 400)
        self.assertIn("NOPE00", body["error"])

    def test_the_form_shows_whom_the_code_names(self):
        """As on «Продажа»: the guest as soon as the code is typed, or «not
        found»; the form takes the name and phone from the answer."""

        from tests.apps.tables.test_reservation_guest import make_guest

        guest = make_guest(self.partner)
        status, body = self.staff_call("reservation_guest",
                                       {"customer_code": guest.customer_code.lower()},
                                       user_id=WAITER_TG)
        self.assertEqual(status, 200, body)
        self.assertEqual((body["guest"], body["name"], body["phone"]),
                         (guest.staff_label(), "Олена Коваль", "+380671234567"))
        status, body = self.staff_call("reservation_guest", {"customer_code": "NOPE00"},
                                       user_id=WAITER_TG)
        self.assertEqual(status, 400)
        self.assertIn("NOPE00", body["error"])
        staff(self.partner, "kava_hookah_code", User.Role.HOOKAH_MASTER, 7700298)
        self.assertEqual(self.staff_call("reservation_guest",
                                         {"customer_code": guest.customer_code},
                                         user_id=7700298)[0], 403)
        self.assertFalse(Reservation.objects.exists())
