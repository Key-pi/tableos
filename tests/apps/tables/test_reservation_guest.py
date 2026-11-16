"""A booking for a guest with the bot (PQ-056, owner 2026-11-15).

The staff may give the guest's customer code: the profile fills in the name
and phone, and «Пришли» seats that guest (their bonuses and history), not an
anonymous company. Without a code — an ordinary visitor, as before.

The bot writes to a guest named by code: a confirmation when the booking is
made (and when its time or table changes) and a reminder before it — each by
its own setting of the «Бронь» module.
"""

import threading
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.partners.models import PartnerBotSettings
from apps.tables import reservations as reservation_services
from apps.tables.models import Reservation, Table, TableSession
from apps.tables.reservations import (
    ReservationError,
    booking_json,
    create_reservation,
    mark_reservation,
    remind_due_reservations,
    seat_reservation,
    update_reservation,
)
from apps.tables.services import activate_table_session, open_staff_table_session
from apps.users.models import GuestProfile, TelegramAccount
from tests.apps.tables.test_reservations import person, venue
from tests.webapp.test_guest_api import make_bot

GUEST_TG = 7791001


def make_guest(partner, telegram_id=GUEST_TG, phone="+380671234567"):
    account = TelegramAccount.objects.create(
        telegram_id=telegram_id, first_name="Олена", last_name="Коваль", username="olena"
    )
    return GuestProfile.objects.create(partner=partner, telegram_account=account, phone=phone)


class Sent:
    """Captures what the bot sends."""

    def __init__(self):
        self.messages = []

    async def __call__(self, _token, messages, **_kwargs):
        self.messages.extend(messages)
        return [(message[0], True, "", False, False) for message in messages]

    def texts(self, telegram_id=GUEST_TG):
        return [text for chat_id, text, *_rest in self.messages if chat_id == telegram_id]


class GuestBookingFixture(TestCase):
    def setUp(self):
        self.partner = venue("Bron")
        self.table = Table.objects.create(partner=self.partner, number=7, seats=4)
        self.other_table = Table.objects.create(partner=self.partner, number=8)
        self.waiter = person(self.partner, "bron_waiter")
        self.guest = make_guest(self.partner)
        self.now = timezone.now().replace(second=0, microsecond=0)
        make_bot(self.partner, username="bron_bot")
        self.sent = Sent()
        patcher = patch("apps.notifications.services._send_telegram_messages", new=self.sent)
        patcher.start()
        self.addCleanup(patcher.stop)

    def book(self, *, code="", at=None, table=None, **fields):
        data = {"guests": 2, "name": "", "phone": "", "comment": ""}
        data.update(fields)
        with self.captureOnCommitCallbacks(execute=True):
            return create_reservation(
                partner_id=self.partner.id, actor=self.waiter,
                table_id=(table or self.table).id,
                starts_at=at or self.now + timedelta(hours=3), minutes=120,
                customer_code=code, **data,
            )

    def update(self, booking, *, code=None, at=None, table=None, **fields):
        data = {"guests": booking.guests, "name": booking.name, "phone": booking.phone,
                "comment": booking.comment}
        data.update(fields)
        with self.captureOnCommitCallbacks(execute=True):
            return update_reservation(
                partner_id=self.partner.id, actor=self.waiter, reservation_id=booking.id,
                table_id=(table or booking.table).id, starts_at=at or booking.starts_at,
                minutes=booking.minutes,
                customer_code=self.guest.customer_code if code is None else code, **data,
            )


class BookingByCodeTests(GuestBookingFixture):
    def test_the_profile_fills_in_the_name_and_phone(self):
        booking = self.book(code=self.guest.customer_code.lower())
        self.assertEqual(booking.guest, self.guest)
        self.assertEqual((booking.name, booking.phone), ("Олена Коваль", "+380671234567"))
        data = booking_json(Reservation.objects.select_related("table", "partner",
                                                               "guest").get(id=booking.id))
        self.assertEqual(data["customer_code"], self.guest.customer_code)

    def test_what_the_staff_typed_wins(self):
        booking = self.book(code=self.guest.customer_code, name="Олена К.", phone="+380990000000")
        self.assertEqual((booking.name, booking.phone), ("Олена К.", "+380990000000"))

    def test_a_guest_without_a_phone_needs_one_typed(self):
        GuestProfile.objects.filter(id=self.guest.id).update(phone="")
        with self.assertRaisesMessage(ReservationError, "телефон"):
            self.book(code=self.guest.customer_code)
        self.assertEqual(self.book(code=self.guest.customer_code, phone="+380501112233").phone,
                         "+380501112233")

    def test_an_unknown_code_is_refused(self):
        with self.assertRaises(ReservationError):
            self.book(code="NOPE00", name="Іван", phone="+380501112233")
        self.assertFalse(Reservation.objects.exists())

    def test_without_a_code_it_is_an_ordinary_visitor(self):
        booking = self.book(name="Іван", phone="+380501112233")
        self.assertIsNone(booking.guest)
        self.assertEqual(self.sent.messages, [])

    def test_the_code_can_be_added_and_removed(self):
        booking = self.book(name="Іван", phone="+380501112233")
        booking = self.update(booking)
        self.assertEqual(booking.guest, self.guest)
        booking = self.update(booking, code="")
        self.assertIsNone(booking.guest)


class ArrivalTests(GuestBookingFixture):
    def test_the_named_guest_sits_at_the_table(self):
        booking = self.book(code=self.guest.customer_code, at=self.now + timedelta(minutes=5))
        booking = seat_reservation(partner_id=self.partner.id, actor=self.waiter,
                                   reservation_id=booking.id)
        session = TableSession.objects.get(visit=booking.visit)
        self.assertEqual((session.guest, session.table, session.status),
                         (self.guest, self.table, TableSession.Status.ACTIVE))
        self.assertFalse(GuestProfile.objects.filter(
            partner=self.partner, telegram_account__isnull=True).exists())

    def test_the_named_guest_joins_the_company_already_seated(self):
        booking = self.book(code=self.guest.customer_code, at=self.now + timedelta(minutes=5))
        company = open_staff_table_session(partner_id=self.partner.id, table_id=self.table.id)
        booking = seat_reservation(partner_id=self.partner.id, actor=self.waiter,
                                   reservation_id=booking.id, visit_id=company.visit_id)
        self.assertEqual(booking.visit_id, company.visit_id)
        self.assertTrue(TableSession.objects.filter(
            visit=company.visit, guest=self.guest, status=TableSession.Status.ACTIVE).exists())

    def test_without_a_code_the_company_is_anonymous(self):
        booking = self.book(name="Іван", phone="+380501112233",
                            at=self.now + timedelta(minutes=5))
        booking = seat_reservation(partner_id=self.partner.id, actor=self.waiter,
                                   reservation_id=booking.id)
        self.assertTrue(TableSession.objects.get(visit=booking.visit).guest.is_anonymous)


class GuestMessageTests(GuestBookingFixture):
    def test_the_guest_gets_a_confirmation(self):
        self.book(code=self.guest.customer_code)
        [text] = self.sent.texts()
        self.assertIn("Бронь подтверждена", text)
        self.assertIn("стол 7", text)

    def test_in_the_guests_language(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(default_language="uk")
        self.book(code=self.guest.customer_code)
        [text] = self.sent.texts()
        self.assertIn("Бронювання підтверджено", text)
        self.assertIn("стіл 7", text)

    def test_no_confirmation_when_the_setting_is_off(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            reservation_confirm_guest=False
        )
        self.book(code=self.guest.customer_code)
        self.assertEqual(self.sent.messages, [])

    def test_a_new_time_or_table_is_told_and_the_reminder_waits_for_it(self):
        booking = self.book(code=self.guest.customer_code)
        Reservation.objects.filter(id=booking.id).update(reminded_at=self.now)
        booking.refresh_from_db()
        booking = self.update(booking, table=self.other_table,
                              at=booking.starts_at + timedelta(hours=1))
        self.assertIn("Бронь изменена", self.sent.texts()[-1])
        self.assertIn("стол 8", self.sent.texts()[-1])
        self.assertIsNone(booking.reminded_at)
        before = len(self.sent.messages)
        self.update(booking, comment="біля вікна")  # nothing the guest needs to know
        self.assertEqual(len(self.sent.messages), before)

    def test_the_reminder_goes_once_before_the_time(self):
        booking = self.book(code=self.guest.customer_code)  # in 3 hours
        self.sent.messages.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(remind_due_reservations(now=self.now + timedelta(minutes=30)), 0)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(remind_due_reservations(now=self.now + timedelta(minutes=65)), 1)
        [text] = self.sent.texts()
        self.assertIn("Напоминаем о брони", text)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(remind_due_reservations(now=self.now + timedelta(minutes=70)), 0)
        booking.refresh_from_db()
        self.assertIsNotNone(booking.reminded_at)

    def test_each_venue_reminds_by_its_own_minutes(self):
        """Another venue reminds four hours ahead: this one still waits for
        its own two hours."""

        other = venue("BronEarly")
        PartnerBotSettings.objects.filter(partner=other).update(reservation_reminder_minutes=240)
        self.book(code=self.guest.customer_code)  # in 3 hours
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(remind_due_reservations(now=self.now + timedelta(minutes=30)), 0)

    def test_no_reminder_when_off_cancelled_or_without_a_guest(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            reservation_reminder_minutes=0
        )
        self.book(code=self.guest.customer_code)
        later = self.now + timedelta(hours=2, minutes=50)
        self.assertEqual(remind_due_reservations(now=later), 0)
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            reservation_reminder_minutes=120
        )
        cancelled = Reservation.objects.get()
        mark_reservation(partner_id=self.partner.id, actor=self.waiter,
                         reservation_id=cancelled.id, status=Reservation.Status.CANCELED)
        self.book(name="Іван", phone="+380501112233")
        self.assertEqual(remind_due_reservations(now=later), 0)

    def test_nothing_for_a_booking_closed_before_the_task_ran(self):
        from apps.notifications.services import send_reservation_notice_now

        booking = self.book(code=self.guest.customer_code)
        mark_reservation(partner_id=self.partner.id, actor=self.waiter,
                         reservation_id=booking.id, status=Reservation.Status.CANCELED)
        self.assertFalse(send_reservation_notice_now(str(booking.id), "reminder"))

    def test_the_reminder_minutes_are_a_setting(self):
        settings = PartnerBotSettings.objects.get(partner=self.partner)
        self.assertEqual((settings.reservation_confirm_guest,
                          settings.reservation_reminder_minutes), (True, 120))


@skipUnless(connection.vendor == "postgresql", "нужны построчные блокировки")
class ArrivalRaceTests(TransactionTestCase):
    """«Пришли» for a named guest while that guest scans the table's QR: the
    guest's row is locked first in both (GuestProfile → Table), so neither
    waits for the other in a circle."""

    def test_seating_and_the_qr_at_once_do_not_deadlock(self):
        partner = venue("BronRace")
        make_bot(partner, username="bron_race_bot")
        table = Table.objects.create(partner=partner, number=3)
        waiter = person(partner, "bron_race_waiter")
        guest = make_guest(partner, telegram_id=7791002)
        with patch("apps.notifications.services._send_telegram_messages", new=Sent()):
            booking = create_reservation(
                partner_id=partner.id, actor=waiter, table_id=table.id,
                starts_at=timezone.now() + timedelta(minutes=5), minutes=120, guests=2,
                name="", phone="", customer_code=guest.customer_code,
            )
        holds_table = threading.Event()
        errors = []
        real_locked = reservation_services._locked_booking

        def locked_then_wait(*args, **kwargs):
            found = real_locked(*args, **kwargs)
            holds_table.set()
            threading.Event().wait(1.5)  # the QR comes in while the table is held
            return found

        def seat():
            try:
                seat_reservation(partner_id=partner.id, actor=waiter, reservation_id=booking.id)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                close_old_connections()

        def scan():
            try:
                holds_table.wait(5)
                activate_table_session(partner_id=partner.id, telegram_id=7791002,
                                       payload=table.deep_link_payload)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                close_old_connections()

        with patch.object(reservation_services, "_locked_booking", locked_then_wait):
            threads = [threading.Thread(target=seat), threading.Thread(target=scan)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual([type(error).__name__ for error in errors], [])
        self.assertEqual(TableSession.objects.filter(
            guest=guest, status=TableSession.Status.ACTIVE).count(), 1)


@skipUnless(connection.vendor == "postgresql", "нужны построчные блокировки")
class ReminderRaceTests(TransactionTestCase):
    """Two beat runs at once remind a booking once: the second waits for the
    first's claim of ``reminded_at`` and then finds it taken."""

    def test_two_runs_at_once_remind_once(self):
        partner = venue("BronRemind")
        make_bot(partner, username="bron_remind_bot")
        table = Table.objects.create(partner=partner, number=4)
        waiter = person(partner, "bron_remind_waiter")
        guest = make_guest(partner, telegram_id=7791003)
        sent = Sent()
        with patch("apps.notifications.services._send_telegram_messages", new=sent):
            create_reservation(
                partner_id=partner.id, actor=waiter, table_id=table.id,
                starts_at=timezone.now() + timedelta(minutes=30), minutes=120, guests=2,
                name="", phone="", customer_code=guest.customer_code,
            )
        sent.messages.clear()
        first_claimed = threading.Event()
        counts, errors = [], []
        from apps.notifications import services as notification_services

        real_notice = notification_services.send_reservation_notice

        def claim_then_wait(*args, **kwargs):
            real_notice(*args, **kwargs)
            if not first_claimed.is_set():
                first_claimed.set()
                threading.Event().wait(1.5)  # the second run comes in meanwhile

        def run(wait_first):
            try:
                if wait_first:
                    first_claimed.wait(5)
                counts.append(remind_due_reservations())
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                close_old_connections()

        with patch("apps.notifications.services._send_telegram_messages", new=sent), \
                patch.object(notification_services, "send_reservation_notice", claim_then_wait):
            threads = [threading.Thread(target=run, args=(False,)),
                       threading.Thread(target=run, args=(True,))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(counts), [0, 1])
        self.assertEqual(len(sent.texts(7791003)), 1)
