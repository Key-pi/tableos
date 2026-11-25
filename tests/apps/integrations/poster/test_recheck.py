"""Open Poster orders re-checked in the background (PQ-061; the safety net
for a webhook Poster never delivered, docs/architecture/POSTER.md, «4.
Статус из Poster»).

Every few minutes Botera reads the register's state of each order it sent
that is still open in Botera and that Poster has said nothing about for a
while — at most for two days after sending — and does what the register
did, as a webhook would have. Orders the register is done with (check
removed or rejected, online order rejected), venues off Poster or
suspended are left alone."""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.billing.models import Payment
from apps.integrations.poster import statuses
from apps.integrations.poster.models import PosterConnection, PosterOrderPush
from apps.integrations.poster.tasks import recheck_open_orders_task
from apps.orders.models import Order
from apps.partners.models import Partner
from tests.apps.integrations.poster.test_statuses import StatusFixture

Push = PosterOrderPush.Status


class RecheckTests(StatusFixture):
    def quiet(self, minutes=15, sent_minutes=None):
        """Poster said nothing about the order for ``minutes``."""

        now = timezone.now()
        PosterOrderPush.objects.filter(pk=self.push_.pk).update(
            updated_at=now - timedelta(minutes=minutes),
            sent_at=now - timedelta(minutes=sent_minutes or minutes),
        )

    def recheck(self):
        with self.captureOnCommitCallbacks(execute=True):
            return statuses.recheck_open_orders()

    def reads(self):
        return [method for method, _params, _body in self.poster.calls
                if method == "incomingOrders.getIncomingOrder"]

    def test_a_lost_accept_is_brought(self):
        self.register(online_status=1)
        self.quiet()
        self.assertEqual(self.recheck(), 1)
        self.assertEqual(self.fresh().status, Order.Status.ACCEPTED)
        self.assertEqual(self.push_.poster_status, 1)

    def test_a_lost_closed_check_is_brought(self):
        self.register(online_status=1, check_status=2, pay_type=1, paid="40000")
        self.quiet()
        self.recheck()
        self.assertEqual(self.fresh().status, Order.Status.COMPLETED)
        payment = Payment.objects.get(bill=self.bill())
        self.assertEqual((payment.method, payment.amount), (Payment.Method.CASH, Decimal("400.00")))

    def test_not_while_poster_was_just_heard(self):
        self.register(online_status=1)
        self.quiet(minutes=5)
        self.assertEqual(self.recheck(), 0)
        self.assertEqual(self.reads(), [])
        self.assertEqual(self.fresh().status, Order.Status.NEW)

    def test_once_read_it_waits_again(self):
        self.register(online_status=0)  # still new at the register
        self.quiet()
        self.assertEqual(self.recheck(), 1)
        self.assertEqual(self.recheck(), 0)
        self.assertEqual(len(self.reads()), 1)

    def test_only_two_days_after_sending(self):
        self.register(online_status=1)
        self.quiet(sent_minutes=60 * 49)
        self.assertEqual(self.recheck(), 0)
        self.quiet(sent_minutes=60 * 47)
        self.assertEqual(self.recheck(), 1)

    def test_closed_in_botera_is_left_alone(self):
        self.register(online_status=1)
        for status in (Order.Status.COMPLETED, Order.Status.CANCELED):
            with self.subTest(status=status):
                Order.objects.filter(pk=self.order_.pk).update(status=status)
                self.quiet()
                self.assertEqual(self.recheck(), 0)
        self.assertEqual(self.reads(), [])

    def test_the_register_done_with_it_is_left_alone(self):
        self.register(online_status=1)
        for fields in ({"check_status": 3}, {"check_status": 4}, {"poster_status": 7}):
            with self.subTest(**fields):
                PosterOrderPush.objects.filter(pk=self.push_.pk).update(
                    check_status=None, poster_status=None)
                PosterOrderPush.objects.filter(pk=self.push_.pk).update(**fields)
                self.quiet()
                self.assertEqual(self.recheck(), 0)
        self.assertEqual(self.reads(), [])

    def test_not_sent_off_poster_or_suspended(self):
        self.register(online_status=1)
        cases = (
            (PosterOrderPush.objects.filter(pk=self.push_.pk), {"status": Push.FAILED},
             {"status": Push.SENT}),
            (PosterConnection.objects.filter(partner=self.partner),
             {"status": PosterConnection.Status.DISCONNECTED},
             {"status": PosterConnection.Status.ACTIVE}),
            (Partner.objects.filter(pk=self.partner.pk),
             {"status": Partner.Status.SUSPENDED}, {"status": Partner.Status.ACTIVE}),
        )
        for rows, off, back in cases:
            with self.subTest(off=off):
                rows.update(**off)
                self.quiet()
                self.assertEqual(self.recheck(), 0)
                rows.update(**back)
        self.assertEqual(self.reads(), [])
        self.quiet()
        self.assertEqual(self.recheck(), 1)  # and back on: read again

    def test_the_task_runs_every_five_minutes(self):
        self.register(online_status=1)
        self.quiet()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(recheck_open_orders_task(), 1)
        entries = [entry for entry in settings.CELERY_BEAT_SCHEDULE.values()
                   if entry["task"] == "poster.recheck_open_orders"]
        self.assertEqual([entry["schedule"] for entry in entries], [timedelta(minutes=5)])
