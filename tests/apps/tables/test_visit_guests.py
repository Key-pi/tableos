"""Guests of a table visit (PQ-009, step 2; owner 2026-11-18).

A company at a table is people, not one anonymous guest: the staff order for
a chosen guest of the visit or for a new guest without the bot («Гость 2»),
and say how many sit at the table. The billing editor (step 3) lays the bill
out by these guests; the day's metrics count the people.
"""

import threading
from datetime import timedelta
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.orders.services import OrderFlowError
from apps.tables.floor_plan import table_states
from apps.tables.models import Table, TableSession, TableVisit
from apps.tables.reservations import create_reservation, seat_reservation
from apps.tables.services import (
    TableSessionError,
    set_visit_guests_count,
    visit_guests,
    visit_headcount,
)
from apps.users.models import User
from tests.apps.orders.test_staff_table_orders import TableOrderFixture, venue
from tests.webapp.test_staff_api import staff


class GuestsWithoutTheBotTests(TableOrderFixture):
    def new_guest(self, visit=None):
        return self.order(visit=visit, new_guest=True)

    def test_each_new_guest_is_a_person_of_the_company(self):
        first = self.order()
        visit_id = first.table_session.visit_id
        second = self.new_guest(visit_id)
        third = self.new_guest(visit_id)
        self.assertEqual(len({first.guest_id, second.guest_id, third.guest_id}), 3)
        self.assertEqual({order.table_session.visit_id for order in (first, second, third)},
                         {visit_id})
        self.assertTrue(all(order.guest.is_anonymous for order in (first, second, third)))
        guests = visit_guests(TableVisit.objects.get(id=visit_id))
        self.assertEqual([guest.label for guest in guests], ["Гость 1", "Гость 2", "Гость 3"])
        self.assertEqual([guest.session_id for guest in guests],
                         [first.table_session_id, second.table_session_id,
                          third.table_session_id])

    def test_a_new_guest_at_a_free_table_starts_the_visit(self):
        order = self.new_guest()
        self.assertEqual(order.table_session.visit.status, TableVisit.Status.ACTIVE)
        self.assertTrue(order.guest.is_anonymous)

    def test_without_a_choice_the_first_guest_without_the_bot_as_before(self):
        first = self.order()
        visit_id = first.table_session.visit_id
        self.new_guest(visit_id)
        again = self.order(visit=visit_id)
        self.assertEqual(again.table_session_id, first.table_session_id)

    def test_an_order_for_a_chosen_guest(self):
        first = self.order()
        visit_id = first.table_session.visit_id
        second = self.new_guest(visit_id)
        more = self.order(visit=visit_id, session=second.table_session_id,
                          items=[{"menu_item_id": self.hookah.id, "quantity": 1}])
        self.assertEqual((more.guest_id, more.table_session_id),
                         (second.guest_id, second.table_session_id))

    def test_the_guests_with_the_bot_are_named(self):
        session = self.scan(7701501)
        self.order(visit=session.visit_id)
        guests = visit_guests(session.visit)
        self.assertEqual([guest.has_bot for guest in guests], [True, False])
        self.assertEqual(guests[0].label, session.guest.staff_label())
        self.assertEqual(guests[1].label, "Гость 1")
        mine = self.order(visit=session.visit_id, session=session.id)
        self.assertEqual(mine.guest_id, session.guest_id)

    def test_a_guest_ordered_for_again_keeps_their_place(self):
        """Ordered for by code once more, or chosen in the list: one session,
        one row in the company's list (it used to close and reopen it)."""

        session = self.scan(7701504)
        by_code = self.order(visit=session.visit_id, code=session.guest.customer_code)
        chosen = self.order(visit=session.visit_id, session=session.id)
        self.assertEqual({by_code.table_session_id, chosen.table_session_id}, {session.id})
        session.refresh_from_db()
        self.assertEqual(session.status, TableSession.Status.ACTIVE)
        self.assertEqual(len(visit_guests(session.visit)), 1)

    def test_a_guest_who_scanned_the_qr_again_is_one_row(self):
        first = self.scan(7701505)
        again = self.scan(7701505)
        self.assertEqual(first.visit_id, again.visit_id)
        guests = visit_guests(first.visit)
        self.assertEqual([guest.session_id for guest in guests], [again.id])
        self.assertTrue(guests[0].active)

    def test_a_guest_of_another_company_or_gone_is_refused(self):
        here = self.order()
        there = self.order(table=self.other_table)
        with self.assertRaises(OrderFlowError):
            self.order(visit=here.table_session.visit_id, session=there.table_session_id)
        session = self.scan(7701502)
        self.scan(7701502, table=self.other_table)  # moved to another table
        with self.assertRaises(OrderFlowError):
            self.order(visit=session.visit_id, session=session.id)

    def test_a_chosen_guest_and_a_code_at_once_is_refused(self):
        first = self.order()
        with self.assertRaises(OrderFlowError):
            self.order(visit=first.table_session.visit_id, session=first.table_session_id,
                       code="ANYONE")
        with self.assertRaises(OrderFlowError):
            self.order(session=first.table_session_id)  # a guest needs their company


class HeadcountTests(TableOrderFixture):
    def test_the_staff_say_how_many_sit_at_the_table(self):
        visit = self.order().table_session.visit
        self.assertEqual(visit_headcount(visit), 1)
        set_visit_guests_count(partner_id=self.partner.id, actor=self.waiter,
                               visit_id=visit.id, count=4)
        visit.refresh_from_db()
        self.assertEqual((visit.guests_count, visit_headcount(visit)), (4, 4))
        self.assertEqual(table_states(self.partner.id)[self.table.id]["guests"], 4)

    def test_never_fewer_than_the_guests_listed(self):
        first = self.order()
        visit = first.table_session.visit
        set_visit_guests_count(partner_id=self.partner.id, actor=self.waiter,
                               visit_id=visit.id, count=1)
        self.order(visit=visit.id, new_guest=True)
        self.order(visit=visit.id, new_guest=True)
        visit.refresh_from_db()
        self.assertEqual(visit_headcount(visit), 3)
        self.assertEqual(table_states(self.partner.id)[self.table.id]["guests"], 3)

    def test_limits_rights_and_venue(self):
        visit = self.order().table_session.visit
        for count in (0, 100, "x"):
            with self.assertRaises(TableSessionError):
                set_visit_guests_count(partner_id=self.partner.id, actor=self.waiter,
                                       visit_id=visit.id, count=count)
        hookah_master = staff(self.partner, "kava_hm", User.Role.HOOKAH_MASTER, 7701503)
        with self.assertRaises(TableSessionError):
            set_visit_guests_count(partner_id=self.partner.id, actor=hookah_master,
                                   visit_id=visit.id, count=3)
        other = venue("Other")
        with self.assertRaises(TableSessionError):
            set_visit_guests_count(partner_id=other.id, actor=self.waiter,
                                   visit_id=visit.id, count=3)
        TableVisit.objects.filter(id=visit.id).update(status=TableVisit.Status.CLOSED)
        with self.assertRaises(TableSessionError):
            set_visit_guests_count(partner_id=self.partner.id, actor=self.waiter,
                                   visit_id=visit.id, count=3)
        visit.refresh_from_db()
        self.assertEqual(visit.guests_count, 0)

    def test_the_booking_says_how_many_came(self):
        from apps.partners.models import PartnerBotSettings

        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_reservations_enabled=True)
        booking = create_reservation(
            partner_id=self.partner.id, actor=self.waiter.user, table_id=self.table.id,
            starts_at=timezone.now() + timedelta(minutes=20), minutes=120, guests=5,
            name="Олена", phone="+380671112233",
        )
        booking = seat_reservation(partner_id=self.partner.id, actor=self.waiter.user,
                                   reservation_id=booking.id)
        self.assertEqual(booking.visit.guests_count, 5)


@skipUnless(connection.vendor == "postgresql", "нужны построчные блокировки")
class NewGuestsAtOnceTests(TransactionTestCase):
    """Two waiters add a new guest to one company at once: two guests, one
    visit — the table's lock makes them take turns."""

    def test_two_new_guests(self):
        from apps.menu.models import MenuItem
        from apps.orders.services import create_staff_table_order

        partner = venue("KavaRace")
        table = Table.objects.create(partner=partner, number=9)
        latte = MenuItem.objects.get(partner=partner, name="Лате")
        waiters = [staff(partner, f"kava_race_{n}", User.Role.WAITER, 7701510 + n)
                   for n in (1, 2)]
        first = create_staff_table_order(
            partner_id=partner.id, actor=waiters[0], table_id=table.id,
            items=[{"menu_item_id": latte.id, "quantity": 1}],
        )
        visit_id = first.table_session.visit_id
        start = threading.Barrier(2)
        errors = []

        def add(waiter):
            try:
                start.wait(5)
                create_staff_table_order(
                    partner_id=partner.id, actor=waiter, table_id=table.id, visit_id=visit_id,
                    new_guest=True, items=[{"menu_item_id": latte.id, "quantity": 1}],
                )
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=add, args=(waiter,)) for waiter in waiters]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(TableSession.objects.filter(visit_id=visit_id).count(), 3)
        self.assertEqual(
            [guest.label for guest in visit_guests(TableVisit.objects.get(id=visit_id))],
            ["Гость 1", "Гость 2", "Гость 3"],
        )
