"""What the Poster register says back (PQ-058, step 6; PQ-059): accepted and
rejected online orders, the closed check paying the Botera bill, and what is
left to staff. Poster is ``FakePoster``; the answers copy the trial account
of step 0: online order 0 new / 1 accepted / 7 rejected; check 0 open /
2 closed / 3 removed / 4 rejected, ``pay_type`` 0 none / 1 cash / 2 card /
3 mixed, sums in kopecks (docs/architecture/POSTER.md, «Итоги шага 0»)."""

import threading
from decimal import Decimal
from unittest import mock, skipUnless

from django.db import connection as db_connection
from django.db import transaction
from django.test import TransactionTestCase, override_settings

from apps.billing.models import Bill, BillAdjustment, Payment, Refund
from apps.billing.remote_orders import cancel_unpaid_remote_order, record_payment_on_receipt
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.integrations.poster import statuses, webhooks
from apps.integrations.poster.models import PosterConnection, PosterOrderPush, PosterWebhookEvent
from apps.notifications.models import StaffNotification
from apps.orders.models import Order, OrderStatusHistory
from tests.apps.integrations.poster.test_orders import CREATED, OrderFixture, OrderSetup
from tests.apps.integrations.poster.test_webhooks import SECRET, signed

Push = PosterOrderPush.Status


def online(status, transaction_id=12):
    return lambda params, body: {"incoming_order_id": 11, "status": status,
                                 "transaction_id": transaction_id, "spot_id": 1}


def check(status, pay_type=0, total="40000", paid="0", reason=""):
    return lambda params, body: [{
        "transaction_id": "12", "status": str(status), "pay_type": str(pay_type),
        "sum": total, "payed_sum": paid, "reason": reason,
    }]


class PosterReadMixin:
    """``statuses`` reads Poster with its own client: the same fake."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(statuses, "PosterClient", self.poster)
        patcher.start()
        self.addCleanup(patcher.stop)


class StatusFixture(PosterReadMixin, OrderFixture):
    """An order of 2 × «Бургер» (400 грн, paid on receipt) sent to Poster:
    online order 11, check 12."""

    def setUp(self):
        super().setUp()
        self.order_ = self.place()
        self.push_ = PosterOrderPush.objects.get(order=self.order_)
        self.assertEqual((self.push_.status, self.push_.transaction_id), (Push.SENT, 12))
        # A voided bill lets go of its order: keep its id.
        self.bill_id = Bill.objects.get(bill_orders__order=self.order_).pk
        self.guest_told = mock.patch("apps.orders.services.send_guest_order_status_update").start()
        self.addCleanup(mock.patch.stopall)

    def register(self, online_status=1, check_status=0, **check_fields):
        self.poster.answers["incomingOrders.getIncomingOrder"] = online(online_status)
        self.poster.answers["dash.getTransaction"] = check(check_status, **check_fields)

    def sync(self):
        with self.captureOnCommitCallbacks(execute=True):
            return statuses.sync_order_state(self.push_.pk)

    def staff_in_botera(self, action, **kwargs):
        """Staff act in Botera while Poster is off (since step 7 they cannot
        while it holds the order); then Poster works again."""

        working = PosterConnection.objects.filter(partner=self.partner)
        working.update(status=PosterConnection.Status.DISCONNECTED)
        try:
            return action(order=self.order_, actor=self.cashier, **kwargs)
        finally:
            working.update(status=PosterConnection.Status.ACTIVE)

    def fresh(self):
        self.order_.refresh_from_db()
        self.push_.refresh_from_db()
        return self.order_

    def bill(self):
        return Bill.objects.get(pk=self.bill_id)

    def attention_messages(self):
        return list(StaffNotification.objects.filter(title__contains="Poster").values_list(
            "message", flat=True))


class AcceptedTests(StatusFixture):
    def test_accepted_at_the_register(self):
        self.register(online_status=1)
        self.sync()
        self.assertEqual(self.fresh().status, Order.Status.ACCEPTED)
        history = OrderStatusHistory.objects.get(order=self.order_, to_status=Order.Status.ACCEPTED)
        self.assertIsNone(history.changed_by)
        self.assertIn("Poster", history.note)
        self.guest_told.assert_called_once()
        self.assertEqual(self.guest_told.call_args.kwargs["to_status"], Order.Status.ACCEPTED)
        self.assertEqual(self.push_.poster_status, 1)

    def test_accepted_once(self):
        self.register(online_status=1)
        self.sync()
        self.sync()
        self.assertEqual(OrderStatusHistory.objects.filter(
            order=self.order_, to_status=Order.Status.ACCEPTED).count(), 1)

    def test_new_at_the_register_changes_nothing(self):
        self.register(online_status=0)
        self.sync()
        self.assertEqual(self.fresh().status, Order.Status.NEW)


class ClosedCheckTests(StatusFixture):
    def test_a_closed_check_pays_the_bill_and_completes_the_order(self):
        self.register(check_status=2, pay_type=1, paid="40000")
        self.sync()
        order = self.fresh()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertIsNotNone(order.paid_at)
        payment = Payment.objects.get(bill=self.bill())
        self.assertEqual(
            (payment.method, payment.amount, payment.provider_code, payment.external_payment_id,
             payment.created_by),
            (Payment.Method.CASH, Decimal("400.00"), "poster", "12", None),
        )
        self.assertEqual(self.bill().status, Bill.Status.PAID)
        self.assertEqual((self.push_.check_status, self.push_.pay_type, self.push_.poster_sum,
                          self.push_.poster_payed_sum, self.push_.attention),
                         (2, 1, Decimal("400.00"), Decimal("400.00"), ""))
        self.assertEqual(self.attention_messages(), [])

    def test_card_and_mixed(self):
        self.register(check_status=2, pay_type=2, paid="40000")
        self.sync()
        self.assertEqual(Payment.objects.get(bill=self.bill()).method, Payment.Method.TERMINAL)
        mixed = self.place()
        push = PosterOrderPush.objects.get(order=mixed)
        self.register(check_status=2, pay_type=3, paid="40000")
        with self.captureOnCommitCallbacks(execute=True):
            statuses.sync_order_state(push.pk)
        self.assertEqual(Payment.objects.get(bill__bill_orders__order=mixed).method,
                         Payment.Method.MIXED)

    def test_paid_once(self):
        self.register(check_status=2, pay_type=1, paid="40000")
        self.sync()
        self.sync()
        self.assertEqual(Payment.objects.filter(bill=self.bill()).count(), 1)

    def test_no_cashback_in_v1(self):
        BonusProgram.objects.create(
            partner=self.partner, name="Cashback", percent="5.00", max_redeem_share="20.00",
            program_type=BonusProgram.ProgramType.CASHBACK,
        )
        self.register(check_status=2, pay_type=1, paid="40000")
        self.sync()
        self.assertFalse(BonusTransaction.objects.filter(
            guest=self.guest, transaction_type=BonusTransaction.TransactionType.ACCRUAL,
        ).exists())
        # A payment staff record (an order without Poster) earns it as before.
        PosterConnection.objects.filter(partner=self.partner).update(
            status=PosterConnection.Status.DISCONNECTED,
        )
        other = self.place()
        record_payment_on_receipt(order=other, actor=self.cashier, method=Payment.Method.CASH)
        self.assertTrue(BonusTransaction.objects.filter(
            guest=self.guest, transaction_type=BonusTransaction.TransactionType.ACCRUAL,
        ).exists())

    def test_a_received_order_gets_the_payment_only(self):
        Order.objects.filter(pk=self.order_.pk).update(status=Order.Status.COMPLETED)
        self.register(check_status=2, pay_type=1, paid="40000")
        self.sync()
        self.assertEqual(self.fresh().status, Order.Status.COMPLETED)
        self.assertEqual(self.bill().status, Bill.Status.PAID)

    def test_a_different_sum_is_marked_and_the_bill_is_paid_as_it_is(self):
        self.register(check_status=2, pay_type=1, total="45000", paid="45000")
        self.sync()
        self.assertEqual(Payment.objects.get(bill=self.bill()).amount, Decimal("400.00"))
        self.fresh()
        self.assertIn("450", self.push_.attention)
        self.assertIn("400", self.push_.attention)
        self.assertEqual(self.attention_messages(), [])  # seen in Admin, not a message

    def test_a_check_still_open_waits(self):
        self.register(online_status=1, check_status=0)
        self.sync()
        self.assertFalse(Payment.objects.filter(bill=self.bill()).exists())


class RegisterTookLessTests(StatusFixture):
    """The register took less money than the Botera bill — a Poster discount,
    an automatic promotion, Poster bonuses, a line the cashier took off. The
    register counts the money: Botera pays the check's sum and keeps the gap
    as its own record (owner's decision 2026-11-24, PQ-061)."""

    def test_the_bill_follows_the_check(self):
        self.register(check_status=2, pay_type=1, total="40000", paid="36000")
        self.sync()
        bill = self.bill()
        self.assertEqual((bill.status, bill.total_amount, bill.discount_amount, bill.paid_amount),
                         (Bill.Status.PAID, Decimal("360.00"), Decimal("40.00"),
                          Decimal("360.00")))
        self.assertEqual(Payment.objects.get(bill=bill).amount, Decimal("360.00"))
        gap = BillAdjustment.objects.get(bill=bill)
        self.assertEqual(
            (gap.kind, gap.actor, gap.total_before, gap.total_after),
            (BillAdjustment.Kind.REGISTER_DIFFERENCE, None, Decimal("400.00"), Decimal("360.00")),
        )
        self.assertIn("№12", gap.reason_text)
        self.assertEqual((gap.payload["register_paid"], gap.payload["difference"],
                          gap.payload["reference"]), ("360.00", "40.00", "12"))
        order = self.fresh()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(self.push_.attention, "")  # nothing left to check
        self.assertEqual(self.attention_messages(), [])

    def test_recorded_once(self):
        self.register(check_status=2, pay_type=1, total="40000", paid="36000")
        self.sync()
        self.sync()
        self.assertEqual(BillAdjustment.objects.filter(bill=self.bill()).count(), 1)
        self.assertEqual(Payment.objects.filter(bill=self.bill()).count(), 1)

    def test_the_same_sum_leaves_no_record(self):
        self.register(check_status=2, pay_type=1, total="40000", paid="40000")
        self.sync()
        self.assertFalse(BillAdjustment.objects.filter(bill=self.bill()).exists())
        self.assertEqual(self.bill().discount_amount, Decimal("0.00"))

    def test_more_than_the_bill_is_still_the_bill_and_marked(self):
        # Something the cashier added is not the Botera order.
        self.register(check_status=2, pay_type=1, total="45000", paid="45000")
        self.sync()
        self.assertFalse(BillAdjustment.objects.filter(bill=self.bill()).exists())
        self.assertEqual(Payment.objects.get(bill=self.bill()).amount, Decimal("400.00"))
        self.fresh()
        self.assertIn("450", self.push_.attention)

    def test_closed_with_no_money_is_left_to_staff(self):
        # The whole check paid by Poster bonuses or a 100 % discount: rare, staff decide.
        self.register(check_status=2, pay_type=1, total="40000", paid="0")
        self.sync()
        self.assertFalse(Payment.objects.filter(bill=self.bill()).exists())
        self.assertFalse(BillAdjustment.objects.filter(bill=self.bill()).exists())
        self.fresh()
        self.assertIn("без оплаты", self.push_.attention)
        self.assertEqual(len(self.attention_messages()), 1)


class LeftToStaffTests(StatusFixture):
    def test_closed_without_payment(self):
        self.register(check_status=2, pay_type=0, reason="1")
        self.sync()
        self.sync()
        order = self.fresh()
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertFalse(Payment.objects.filter(bill=self.bill()).exists())
        self.assertIn("без оплаты", self.push_.attention)
        [message] = self.attention_messages()  # once
        self.assertIn("без оплаты", message)

    def test_a_removed_check_of_money_taken_in_botera(self):
        # Staff took the money in Botera while Poster was off: it is not the register's to undo.
        self.staff_in_botera(record_payment_on_receipt, method=Payment.Method.CASH)
        self.register(check_status=3, pay_type=1, paid="40000")
        self.sync()
        self.fresh()
        self.assertIn("удалён", self.push_.attention)
        self.assertEqual(len(self.attention_messages()), 1)
        self.assertFalse(Refund.objects.filter(bill=self.bill()).exists())
        self.assertEqual(self.bill().status, Bill.Status.PAID)

    def test_a_paid_check_of_an_order_canceled_in_botera(self):
        self.staff_in_botera(cancel_unpaid_remote_order)
        self.register(check_status=2, pay_type=1, paid="40000")
        self.sync()
        self.assertFalse(Payment.objects.filter(bill__bill_orders__order=self.order_,
                                                status=Payment.Status.PAID).exists())
        self.fresh()
        self.assertIn("отменён", self.push_.attention)
        self.assertEqual(len(self.attention_messages()), 1)


class RemovedCheckTests(StatusFixture):
    """The check deleted in Poster's admin panel — how a non-fiscal check is
    returned or canceled; with a fiscal registrar after «Повернення» too
    (PQ-061 checks: ``status`` 3 and ``transaction removed`` a second later,
    the sums stay). The register counts the money: Botera repeats it (owner
    2026-11-24)."""

    def setUp(self):
        super().setUp()
        self.refund_notice = mock.patch(
            "apps.notifications.services.send_guest_refund_notice").start()

    def paid_then_removed(self, paid="40000"):
        self.register(check_status=2, pay_type=1, paid=paid)
        self.sync()
        self.register(check_status=3, pay_type=1, paid=paid)
        return self.sync()

    def test_a_paid_check_removed_is_a_refund_at_the_register(self):
        self.assertEqual(self.paid_then_removed(), "refunded")
        refund = Refund.objects.get(bill=self.bill())
        self.assertEqual(
            (refund.method, refund.actor, refund.is_full, refund.money_amount,
             refund.reason_code),
            (Refund.Method.REGISTER, None, True, Decimal("400.00"), Refund.ReasonCode.OTHER),
        )
        self.assertIn("№12", refund.reason_text)
        self.assertEqual(self.bill().refunded_amount, Decimal("400.00"))
        self.assertEqual(self.fresh().status, Order.Status.COMPLETED)  # as any refund of it
        self.assertEqual(self.push_.attention, "")
        self.assertEqual(self.attention_messages(), [])
        self.refund_notice.assert_called_once_with(refund)

    def test_refunded_once(self):
        self.paid_then_removed()
        self.assertEqual(self.sync(), "")
        self.assertEqual(Refund.objects.filter(bill=self.bill()).count(), 1)

    def test_the_money_the_register_took_comes_back(self):
        self.paid_then_removed(paid="36000")
        self.assertEqual(Refund.objects.get(bill=self.bill()).money_amount, Decimal("360.00"))

    def test_an_open_check_removed_cancels_the_order(self):
        self.register(online_status=1, check_status=0)
        self.sync()
        self.register(online_status=1, check_status=3)
        self.assertEqual(self.sync(), "canceled")
        self.assertEqual(self.fresh().status, Order.Status.CANCELED)
        self.assertEqual(self.bill().status, Bill.Status.CANCELED)
        self.assertEqual(self.guest_told.call_args.kwargs["to_status"], Order.Status.CANCELED)
        history = OrderStatusHistory.objects.get(order=self.order_, to_status=Order.Status.CANCELED)
        self.assertIn("№12", history.note)
        self.assertEqual(self.attention_messages(), [])
        self.assertEqual(self.sync(), "")  # again: nothing

    def test_received_by_the_guest_and_never_paid_is_left_to_staff(self):
        Order.objects.filter(pk=self.order_.pk).update(status=Order.Status.COMPLETED)
        self.register(online_status=1, check_status=3)
        self.sync()
        self.assertEqual(self.fresh().status, Order.Status.COMPLETED)
        self.assertIn("удалён", self.push_.attention)
        self.assertEqual(len(self.attention_messages()), 1)

    def test_staff_cannot_pick_the_register(self):
        from apps.billing.services import BillingServiceError, refund_bill_lines
        from apps.users.models import User

        owner = User.objects.create_user(username="poster_owner", password="x",
                                         partner=self.partner, role=User.Role.OWNER)
        PosterConnection.objects.filter(partner=self.partner).update(
            status=PosterConnection.Status.DISCONNECTED)
        record_payment_on_receipt(order=self.order_, actor=self.cashier,
                                  method=Payment.Method.CASH)
        bill = self.bill()
        with self.assertRaises(BillingServiceError):
            refund_bill_lines(bill=bill, bill_item_ids=[i.id for i in bill.items.all()],
                              actor=owner, reason_code=Refund.ReasonCode.OTHER,
                              method=Refund.Method.REGISTER)
        self.assertFalse(Refund.objects.filter(bill=bill).exists())
        # The owner refunds this way as before: the right is not what stopped it.
        refund_bill_lines(bill=bill, bill_item_ids=[i.id for i in bill.items.all()],
                          actor=owner, reason_code=Refund.ReasonCode.OTHER,
                          method=Refund.Method.CASH)
        self.assertTrue(Refund.objects.filter(bill=bill).exists())


class RejectedTests(StatusFixture):
    def test_rejected_at_the_register(self):
        self.register(online_status=7, check_status=4)
        self.sync()
        self.assertEqual(self.fresh().status, Order.Status.CANCELED)
        self.assertEqual(self.bill().status, Bill.Status.CANCELED)
        self.assertEqual(self.guest_told.call_args.kwargs["to_status"], Order.Status.CANCELED)
        history = OrderStatusHistory.objects.get(order=self.order_, to_status=Order.Status.CANCELED)
        self.assertIn("Poster", history.note)
        self.assertEqual(self.attention_messages(), [])

    def test_rejected_after_staff_took_the_money(self):
        self.staff_in_botera(record_payment_on_receipt, method=Payment.Method.CASH)
        self.register(online_status=7, check_status=4)
        self.sync()
        self.assertNotEqual(self.fresh().status, Order.Status.CANCELED)
        self.assertIn("отклон", self.push_.attention)
        self.assertEqual(len(self.attention_messages()), 1)


class ReadingPosterTests(StatusFixture):
    def test_a_temporary_failure_tries_again(self):
        from apps.integrations.poster.client import PosterError

        self.poster.answers["incomingOrders.getIncomingOrder"] = PosterError(
            "Poster did not answer in time", temporary=True)
        with mock.patch.object(statuses, "_retry_later") as later:
            self.sync()
        later.assert_called_once()
        self.assertEqual(self.fresh().status, Order.Status.NEW)

    def test_nothing_without_a_working_connection(self):
        PosterConnection.objects.filter(pk=self.connection.pk).update(
            status=PosterConnection.Status.DISCONNECTED)
        self.register(online_status=1)
        calls = len(self.poster.calls)
        self.sync()
        self.assertEqual(len(self.poster.calls), calls)
        self.assertEqual(self.fresh().status, Order.Status.NEW)


@override_settings(POSTER_APPLICATION_SECRET=SECRET)
class WebhookRoutingTests(StatusFixture):
    def receive(self, **fields):
        with self.captureOnCommitCallbacks(execute=True):
            event = webhooks.receive(signed(**fields))
        event.refresh_from_db()
        return event

    def test_an_event_of_our_online_order_brings_its_state(self):
        self.register(online_status=1)
        event = self.receive(object="incoming_order", object_id=11, action="changed",
                             data='{"type":1}')
        self.assertEqual((event.status, event.partner), (PosterWebhookEvent.Status.PROCESSED,
                                                         self.partner))
        self.assertEqual(self.fresh().status, Order.Status.ACCEPTED)

    def test_an_event_of_our_check_brings_its_state(self):
        self.register(check_status=2, pay_type=1, paid="40000")
        self.receive(object="transaction", object_id=12, action="closed")
        self.assertEqual(self.fresh().status, Order.Status.COMPLETED)

    def test_another_accounts_order_with_the_same_number_is_not_touched(self):
        from apps.partners.models import Partner
        from tests.apps.integrations.poster.test_menu_sync import connect

        PosterConnection.objects.filter(pk=self.connection.pk).update(account="other")
        neighbour = Partner.objects.create(name="Kava 2", slug="poster-status-neighbour",
                                           status=Partner.Status.ACTIVE)
        connect(neighbour, spot_id=2)  # account «kava», no order 11 of its own
        self.register(online_status=1)
        event = self.receive(object="incoming_order", object_id=11, action="changed",
                             data='{"type":1}')
        self.assertEqual(event.status, PosterWebhookEvent.Status.IGNORED)
        self.assertEqual(self.fresh().status, Order.Status.NEW)

    def test_a_check_not_from_botera_is_not_ours(self):
        calls = len(self.poster.calls)
        event = self.receive(object="transaction", object_id=999, action="closed")
        self.assertEqual(event.status, PosterWebhookEvent.Status.IGNORED)
        self.assertEqual(len(self.poster.calls), calls)


class NetworkOutsideTransactionTests(PosterReadMixin, OrderSetup, TransactionTestCase):
    def test_poster_is_read_outside_a_transaction(self):
        order = self.order()
        PosterOrderPush.objects.create(partner=self.partner, order=order, status=Push.SENT,
                                       incoming_order_id=11, transaction_id=12)
        seen = []

        def read(answer):
            def inner(params, body):
                seen.append(transaction.get_connection().in_atomic_block)
                return answer(params, body)
            return inner

        self.poster.answers["incomingOrders.getIncomingOrder"] = read(online(1))
        self.poster.answers["dash.getTransaction"] = read(check(0))
        statuses.sync_order_state(PosterOrderPush.objects.get(order=order).pk)
        self.assertEqual(seen, [False, False])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.ACCEPTED)


@skipUnless(db_connection.vendor == "postgresql", "нужны построчные блокировки")
class TwoEventsAtOnceTests(PosterReadMixin, OrderSetup, TransactionTestCase):
    """Poster sends the online order's and the check's webhooks a second
    apart: two syncs of one order at once pay the bill once."""

    def test_paid_once(self):
        mock.patch("apps.orders.services.send_guest_order_status_update").start()
        self.addCleanup(mock.patch.stopall)
        self._cart()
        order = self._place_on_receipt()
        push = PosterOrderPush.objects.get(order=order)
        self.poster.answers["incomingOrders.getIncomingOrder"] = online(1)
        self.poster.answers["dash.getTransaction"] = check(2, pay_type=1, paid="40000")
        self._two_at_once(push)
        self.assertEqual(Payment.objects.filter(bill__bill_orders__order=order,
                                                status=Payment.Status.PAID).count(), 1)

    def test_a_removed_check_refunded_once(self):
        """The check deleted: two syncs at once give one «На кассе» refund."""

        mock.patch("apps.orders.services.send_guest_order_status_update").start()
        mock.patch("apps.notifications.services.send_guest_refund_notice").start()
        self.addCleanup(mock.patch.stopall)
        self._cart()
        order = self._place_on_receipt()
        push = PosterOrderPush.objects.get(order=order)
        self.poster.answers["incomingOrders.getIncomingOrder"] = online(1)
        self.poster.answers["dash.getTransaction"] = check(2, pay_type=1, paid="40000")
        statuses.sync_order_state(push.pk)
        self.poster.answers["dash.getTransaction"] = check(3, pay_type=1, paid="40000")
        self._two_at_once(push)
        self.assertEqual(Refund.objects.filter(bill__bill_orders__order=order).count(), 1)
        push.refresh_from_db()
        self.assertEqual(push.attention, "")  # the second saw the first under the lock

    def _two_at_once(self, push):
        barrier = threading.Barrier(2)
        read = statuses._read_state

        def read_and_wait(*args, **kwargs):
            state = read(*args, **kwargs)
            barrier.wait(timeout=5)
            return state

        errors = []

        def run():
            try:
                statuses.sync_order_state(push.pk)
            except Exception as exc:  # noqa: BLE001 — the test reports it
                errors.append(exc)
            finally:
                db_connection.close()

        with mock.patch.object(statuses, "_read_state", side_effect=read_and_wait):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])

    def _place_on_receipt(self):
        from apps.billing.remote_orders import place_remote_order
        from tests.apps.billing.test_remote_orders import NOON, TG

        self.poster.answers["incomingOrders.createIncomingOrder"] = dict(CREATED)
        return place_remote_order(
            partner_id=self.partner.id, telegram_id=TG, slot="now", use_bonuses=False,
            expected_total="400.00", expected_to_pay="400.00",
            payment=Order.PaymentTiming.ON_RECEIPT, now=NOON,
        )
