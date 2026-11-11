"""Reservations in Admin (PQ-052, step 3): the list for the venue, a form
checked by the same service (no double booking), «Пришли», «Не пришли»,
«Отменить» as buttons."""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from apps.tables.models import Reservation
from apps.users.constants import AdminAccessPreset
from tests.apps.tables.test_reservations import ReservationFixture, venue
from tests.apps.users.test_admin_navigation import admin_user


class ReservationAdminTests(ReservationFixture):
    def setUp(self):
        super().setUp()
        self.owner = admin_user(self.partner, "res_owner", AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(self.owner)

    def post_form(self, starts_at, **extra):
        local = timezone.localtime(starts_at)
        return self.client.post(reverse("admin:tables_reservation_add"), {
            "table": self.table.pk, "starts_at_0": local.strftime("%Y-%m-%d"),
            "starts_at_1": local.strftime("%H:%M"), "minutes": 120, "guests": 3,
            "name": "Петро", "phone": "+380931112233", "comment": "", **extra,
        })

    def test_booked_from_admin_and_checked_by_the_service(self):
        at = self.now + timedelta(days=1)
        response = self.post_form(at)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None)
                         and response.context["adminform"].form.errors)
        booking = Reservation.objects.get()
        self.assertEqual((booking.created_by, booking.status), (self.owner, "booked"))
        response = self.post_form(at + timedelta(minutes=30))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "уже забронирован")
        self.assertEqual(Reservation.objects.count(), 1)

    def test_buttons(self):
        booking = self.book(at=self.now + timedelta(minutes=30), actor=self.owner)
        page = self.client.get(reverse("admin:tables_reservation_change", args=[booking.pk]))
        codes = [action.code for action in page.context["object_actions"]]
        self.assertEqual(codes, ["seat", "cancel"])  # not «no show» before the time
        self.client.post(reverse("admin:tables_reservation_act", args=[booking.pk]),
                         {"object_action": "seat"})
        booking.refresh_from_db()
        self.assertEqual(booking.status, Reservation.Status.SEATED)

    def test_only_this_venue(self):
        other = venue("Other")
        from apps.tables.models import Table

        foreign = Table.objects.create(partner=other, number=1)
        from apps.tables.reservations import create_reservation
        from tests.apps.tables.test_reservations import person

        booking = create_reservation(
            partner_id=other.id, actor=person(other, "other_w"), table_id=foreign.id,
            starts_at=self.now + timedelta(days=2), minutes=120, guests=2, name="X",
            phone="+380", comment="",
        )
        self.assertEqual(self.client.get(
            reverse("admin:tables_reservation_change", args=[booking.pk])).status_code, 302)
        listed = self.client.get(reverse("admin:tables_reservation_changelist"))
        self.assertNotContains(listed, "+380")
