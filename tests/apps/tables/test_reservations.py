"""Reservations (PQ-052, step 3, owner 2026-11-11).

Only the staff screen and Admin book tables: name, phone, guests, time,
duration (2 hours by default), comment. The server never lets one table be
booked twice for overlapping time. An hour before, the table shows as
reserved on the plan; 15 minutes after the time with nobody seated it is
highlighted, and «не пришли» is marked by hand. «Пришли» opens the visit.
A module of its own (``module_reservations_enabled``)."""

import threading
from datetime import timedelta
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.partners.models import PartnerBotSettings
from apps.tables.floor_plan import table_alerts, table_states
from apps.tables.models import Reservation, Table, TableVisit
from apps.tables.reservations import (
    ReservationError,
    create_reservation,
    mark_reservation,
    seat_reservation,
    update_reservation,
)
from apps.tables.services import activate_table_session
from apps.users.models import User
from tests.webapp.test_guest_api import make_venue


def venue(name="Kava"):
    partner = make_venue(name)
    PartnerBotSettings.objects.filter(partner=partner).update(
        module_floor_plan_enabled=True, module_reservations_enabled=True,
    )
    return partner


def person(partner, username, role=User.Role.WAITER, **extra):
    return User.objects.create_user(username=username, password="x", partner=partner,
                                    role=role, **extra)


class ReservationFixture(TestCase):
    def setUp(self):
        self.partner = venue()
        self.table = Table.objects.create(partner=self.partner, number=5, seats=4)
        self.other_table = Table.objects.create(partner=self.partner, number=6)
        self.waiter = person(self.partner, "kava_waiter")
        self.now = timezone.now().replace(second=0, microsecond=0)

    def book(self, *, at=None, minutes=120, table=None, actor=None, **fields):
        data = {"guests": 4, "name": "Іван", "phone": "+380501112233", "comment": ""}
        data.update(fields)
        return create_reservation(
            partner_id=self.partner.id, actor=actor or self.waiter,
            table_id=(table or self.table).id,
            starts_at=at or self.now + timedelta(hours=3), minutes=minutes, **data,
        )


class BookingTests(ReservationFixture):
    def test_a_table_is_booked(self):
        booking = self.book(comment="день народження")
        self.assertEqual((booking.status, booking.minutes, booking.guests),
                         (Reservation.Status.BOOKED, 120, 4))
        self.assertEqual(booking.ends_at, booking.starts_at + timedelta(hours=2))
        self.assertEqual(booking.created_by, self.waiter)

    def test_one_table_is_never_booked_twice_at_once(self):
        first = self.book()
        with self.assertRaisesMessage(ReservationError, "уже забронирован"):
            self.book(at=first.starts_at + timedelta(minutes=90))
        with self.assertRaises(ReservationError):
            self.book(at=first.starts_at - timedelta(minutes=30))
        # Right after it, another table, or once it is cancelled — fine.
        self.book(at=first.ends_at)
        self.book(at=first.starts_at, table=self.other_table)
        mark_reservation(partner_id=self.partner.id, actor=self.waiter,
                         reservation_id=first.id, status=Reservation.Status.CANCELED)
        self.book(at=first.starts_at + timedelta(minutes=30), minutes=60)

    def test_what_a_booking_needs(self):
        cases = {
            "no name": {"name": "  "},
            "no phone": {"phone": ""},
            "no guests": {"guests": 0},
            "too short": {"minutes": 10},
            "too long": {"minutes": 900},
            "in the past": {"at": self.now - timedelta(hours=1)},
        }
        for case, fields in cases.items():
            with self.subTest(case), self.assertRaises(ReservationError):
                self.book(**fields)
        self.assertFalse(Reservation.objects.exists())

    def test_who_books(self):
        for role in (User.Role.OWNER, User.Role.MANAGER, User.Role.CASHIER):
            self.book(actor=person(self.partner, f"kava_{role}", role),
                      at=self.now + timedelta(days=1, hours=len(Reservation.objects.all()) * 3))
        with self.assertRaisesMessage(ReservationError, "официант"):
            self.book(actor=person(self.partner, "kava_hookah", User.Role.HOOKAH_MASTER))
        other = venue("Other")
        with self.assertRaises(ReservationError):
            self.book(actor=person(other, "other_waiter"))
        foreign_table = Table.objects.create(partner=other, number=1)
        with self.assertRaises(ReservationError):
            self.book(table=foreign_table)
        self.waiter.is_active = False
        self.waiter.save()
        with self.assertRaises(ReservationError):
            self.book()

    def test_the_module_is_checked(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_reservations_enabled=False
        )
        with self.assertRaisesMessage(ReservationError, "выключен"):
            self.book()

    def test_needs_the_floor_plan(self):
        from django.core.exceptions import ValidationError

        settings = PartnerBotSettings.objects.get(partner=self.partner)
        settings.module_floor_plan_enabled = False
        with self.assertRaises(ValidationError) as caught:
            settings.full_clean()
        self.assertIn("module_reservations_enabled", str(caught.exception))

    def test_a_booking_is_moved_without_clashing_with_itself(self):
        booking = self.book()
        other = self.book(at=booking.ends_at + timedelta(hours=1))
        moved = update_reservation(
            partner_id=self.partner.id, actor=self.waiter, reservation_id=booking.id,
            table_id=self.table.id, starts_at=booking.starts_at + timedelta(minutes=30),
            minutes=120, guests=6, name="Іван", phone="+380501112233", comment="",
        )
        self.assertEqual((moved.guests, moved.starts_at),
                         (6, booking.starts_at + timedelta(minutes=30)))
        with self.assertRaises(ReservationError):
            update_reservation(
                partner_id=self.partner.id, actor=self.waiter, reservation_id=booking.id,
                table_id=self.table.id, starts_at=other.starts_at, minutes=60, guests=6,
                name="Іван", phone="+380501112233", comment="",
            )


    def test_a_late_booking_keeps_its_time_when_the_phone_is_fixed(self):
        booking = self.book(at=self.now + timedelta(minutes=5))
        past = self.now - timedelta(minutes=40)
        Reservation.objects.filter(id=booking.id).update(starts_at=past,
                                                         ends_at=past + timedelta(hours=2))
        fixed = update_reservation(
            partner_id=self.partner.id, actor=self.waiter, reservation_id=booking.id,
            table_id=self.table.id, starts_at=past, minutes=120, guests=4, name="Іван",
            phone="+380509999999", comment="",
        )
        self.assertEqual(fixed.phone, "+380509999999")
        with self.assertRaisesMessage(ReservationError, "уже прошло"):
            update_reservation(
                partner_id=self.partner.id, actor=self.waiter, reservation_id=booking.id,
                table_id=self.table.id, starts_at=past - timedelta(minutes=30), minutes=120,
                guests=4, name="Іван", phone="+380509999999", comment="",
            )


class ArrivalTests(ReservationFixture):
    def test_they_came_the_visit_opens(self):
        booking = self.book(at=self.now + timedelta(minutes=20))
        seated = seat_reservation(partner_id=self.partner.id, actor=self.waiter,
                                  reservation_id=booking.id)
        self.assertEqual(seated.status, Reservation.Status.SEATED)
        self.assertEqual(seated.visit.status, TableVisit.Status.ACTIVE)
        self.assertEqual(seated.visit.table_id, self.table.id)
        self.assertEqual(table_states(self.partner.id)[self.table.id]["state"], "busy")
        with self.assertRaises(ReservationError):
            seat_reservation(partner_id=self.partner.id, actor=self.waiter,
                             reservation_id=booking.id)
        with self.assertRaises(ReservationError):
            mark_reservation(partner_id=self.partner.id, actor=self.waiter,
                             reservation_id=booking.id, status=Reservation.Status.CANCELED)

    def test_the_table_is_still_taken(self):
        activate_table_session(partner_id=self.partner.id, telegram_id=880301,
                               payload=self.table.deep_link_payload)
        booking = self.book(at=self.now + timedelta(minutes=10))
        with self.assertRaisesMessage(ReservationError, "ещё сидят"):
            seat_reservation(partner_id=self.partner.id, actor=self.waiter,
                             reservation_id=booking.id)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Reservation.Status.BOOKED)

    def test_they_were_seated_before_the_booking_was_marked(self):
        """The waiter seated the company first; «Это гости брони?» — yes: the
        booking joins their visit, no second visit opens."""

        session = activate_table_session(partner_id=self.partner.id, telegram_id=880303,
                                         payload=self.table.deep_link_payload)
        booking = self.book(at=self.now + timedelta(minutes=5))
        seated = seat_reservation(partner_id=self.partner.id, actor=self.waiter,
                                  reservation_id=booking.id, visit_id=session.visit_id)
        self.assertEqual((seated.status, seated.visit_id),
                         (Reservation.Status.SEATED, session.visit_id))
        self.assertEqual(TableVisit.objects.filter(table=self.table).count(), 1)
        # A company at another table is not «them».
        other_visit = TableVisit.objects.create(partner=self.partner, table=self.other_table)
        later = self.book(at=self.now + timedelta(hours=6))
        with self.assertRaisesMessage(ReservationError, "ушла"):
            seat_reservation(partner_id=self.partner.id, actor=self.waiter,
                             reservation_id=later.id, visit_id=other_visit.id)

    def test_once_the_company_leaves_the_rest_of_its_time_is_free(self):
        booking = self.book(at=self.now + timedelta(minutes=5))
        seated = seat_reservation(partner_id=self.partner.id, actor=self.waiter,
                                  reservation_id=booking.id)
        with self.assertRaisesMessage(ReservationError, "уже забронирован"):
            self.book(at=self.now + timedelta(minutes=60))
        TableVisit.objects.filter(id=seated.visit_id).update(status=TableVisit.Status.CLOSED)
        self.book(at=self.now + timedelta(minutes=60))

    def test_no_show_only_once_the_time_has_come(self):
        soon = self.book(at=self.now + timedelta(minutes=30))
        with self.assertRaisesMessage(ReservationError, "ещё не наступило"):
            mark_reservation(partner_id=self.partner.id, actor=self.waiter,
                             reservation_id=soon.id, status=Reservation.Status.NO_SHOW)
        Reservation.objects.filter(id=soon.id).update(starts_at=self.now - timedelta(minutes=20))
        marked = mark_reservation(partner_id=self.partner.id, actor=self.waiter,
                                  reservation_id=soon.id, status=Reservation.Status.NO_SHOW)
        self.assertEqual((marked.status, marked.closed_by), (Reservation.Status.NO_SHOW,
                                                             self.waiter))


class PlanTests(ReservationFixture):
    def at(self, booking, minutes_from_start):
        """Moves the clock: the booking started ``minutes_from_start`` ago."""

        start = self.now - timedelta(minutes=minutes_from_start)
        Reservation.objects.filter(id=booking.id).update(
            starts_at=start, ends_at=start + timedelta(minutes=booking.minutes)
        )

    def test_an_hour_before_the_table_shows_reserved(self):
        booking = self.book(at=self.now + timedelta(minutes=70))
        self.assertEqual(table_states(self.partner.id)[self.table.id]["state"], "free")
        self.at(booking, -50)
        state = table_states(self.partner.id)[self.table.id]
        self.assertEqual(state["state"], "reserved")
        self.assertEqual((state["reservation"]["name"], state["reservation"]["guests"]),
                         ("Іван", 4))
        self.at(booking, 10)  # a little late: still just reserved
        self.assertEqual(table_states(self.partner.id)[self.table.id]["state"], "reserved")
        self.assertEqual(table_alerts(self.partner.id, reservations=True), [])

    def test_fifteen_minutes_late_it_lights_up(self):
        booking = self.book(at=self.now + timedelta(hours=1))
        self.at(booking, 20)
        self.assertEqual(table_states(self.partner.id)[self.table.id]["state"], "late")
        self.assertEqual(table_alerts(self.partner.id, reservations=True),
                         [{"table": 5, "kind": "late"}])
        # Over: not on the plan any more.
        self.at(booking, 130)
        self.assertEqual(table_states(self.partner.id)[self.table.id]["state"], "free")

    def test_without_the_module_the_plan_shows_no_bookings(self):
        self.book(at=self.now + timedelta(minutes=20))
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_reservations_enabled=False
        )
        state = table_states(self.partner.id)[self.table.id]
        self.assertEqual((state["state"], state["reservation"]), ("free", None))

    def test_guests_at_the_table_and_a_booking_soon(self):
        activate_table_session(partner_id=self.partner.id, telegram_id=880302,
                               payload=self.table.deep_link_payload)
        self.book(at=self.now + timedelta(minutes=40))
        state = table_states(self.partner.id)[self.table.id]
        self.assertEqual(state["state"], "busy")
        self.assertEqual(state["reservation"]["name"], "Іван")


@skipUnless(connection.vendor == "postgresql", "PostgreSQL only")
class DoubleBookingRaceTests(TransactionTestCase):
    """Two people book one table for the same evening at once: the table row
    is locked first, so the second sees the first booking."""

    def test_one_booking_wins(self):
        partner = venue("Race")
        table = Table.objects.create(partner=partner, number=1)
        people = [person(partner, f"race_{n}") for n in (1, 2)]
        at = timezone.now() + timedelta(hours=5)
        barrier = threading.Barrier(2)
        results = []

        def book(actor):
            try:
                barrier.wait()
                create_reservation(partner_id=partner.id, actor=actor, table_id=table.id,
                                   starts_at=at, minutes=120, guests=2, name="Race",
                                   phone="+380500000000", comment="")
                results.append("ok")
            except ReservationError:
                results.append("taken")
            finally:
                close_old_connections()

        threads = [threading.Thread(target=book, args=(actor,)) for actor in people]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), ["ok", "taken"])
        self.assertEqual(Reservation.objects.filter(table=table).count(), 1)
