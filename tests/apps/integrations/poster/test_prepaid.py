"""Paid in advance by transfer through the Poster register (PQ-061, v2;
owner 2026-11-24, docs/architecture/POSTER.md, «Предоплата»).

The order goes to the register at once, with the payment and Botera's own
prices (the check equals what the guest transferred) and a comment for the
cashier. «Прийняти» at the register confirms the transfer: Botera records it
and takes the order into work; the closed check completes it. Rejected
before that — canceled, the guest hears the venue returns the money, staff
are told to return it. While the register holds the order, nobody in
Botera confirms or rejects the transfer, and the timer does not cancel it.
Poster is ``FakePoster``; a closed fully prepaid check copies the trial
account (spike P1: ``pay_type`` 3, ``payed_third_party`` = the sum)."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock, skipUnless

from django.contrib import admin
from django.db import connection as db_connection
from django.test import RequestFactory, TransactionTestCase
from django.utils import timezone

from apps.billing.admin import BillAdmin
from apps.billing.models import Bill, Payment, Refund
from apps.billing.remote_orders import (
    RemoteOrderError,
    cancel_expired_transfer_orders,
    confirm_transfer,
    payment_modes_for,
    place_remote_order,
    reject_transfer,
)
from apps.billing.services import BillingServiceError, refund_bill_lines
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.integrations.poster.models import PosterConnection, PosterOrderPush
from apps.integrations.poster.selectors import register_holds
from apps.notifications.models import StaffNotification
from apps.notifications.services import _remote_status_note
from apps.orders.models import Order, OrderStatusHistory
from apps.orders.repositories import OrderRepository
from apps.partners.models import PartnerBotSettings
from apps.users.models import User
from bot.handlers.staff import _order_actions_keyboard, _render_order_card
from core.i18n import texts
from tests.apps.billing.test_remote_orders import NOON, TG
from tests.apps.integrations.poster.test_orders import CREATED, OrderFixture, OrderSetup
from tests.apps.integrations.poster.test_statuses import (
    PosterReadMixin,
    StatusFixture,
    check,
    online,
)
from tests.bot.test_guest_flow import _data
from webapp.staff_billing import _refund_order
from webapp.staff_views import _order_json

Push = PosterOrderPush.Status
PREPAID, ON_RECEIPT = Order.PaymentTiming.PREPAID, Order.PaymentTiming.ON_RECEIPT
REQUISITES = "Карта 0000 0000 0000 0000, ФОП Тест"


class SentTests(OrderFixture):
    """What goes to the register: the payment and Botera's prices."""

    def test_paid_in_advance_goes_with_the_payment(self):
        order = self.order(payment=PREPAID)
        push = self.push(order)
        self.assertEqual(push.status, Push.SENT)
        [body] = self.poster.created()
        # Kopecks; the check equals the transfer: «До оплати 0» at the register.
        self.assertEqual(body["payment"], {"type": 1, "sum": 20000, "currency": "UAH"})
        self.assertEqual(body["products"], [{"product_id": "1", "count": 1, "price": 20000}])
        self.assertIn("Оплачено переводом: 200.00 грн", body["comment"])
        self.assertIn(f"Botera #{order.public_id}", body["comment"])

    def test_each_line_at_the_price_the_guest_paid(self):
        """Per unit, the whole line with its add-ons (spike C4), whatever
        Poster's price is now."""

        self.push(self.order(payment=PREPAID, lines=[
            {"quantity": 2},
            {"quantity": 1, "modifiers": []},
        ]))
        body = self.poster.created()[0]
        self.assertEqual([line["price"] for line in body["products"]], [20000, 20000])
        self.assertEqual(body["payment"]["sum"], 60000)

    def test_a_prepaid_delivery_pays_the_delivery_too(self):
        self.push(self.order(payment=PREPAID, channel=Order.Channel.DELIVERY,
                             delivery_address="вул. Тестова, 1", fee="50.00"))
        body = self.poster.created()[0]
        self.assertEqual(body["delivery_price"], 5000)
        self.assertEqual(body["payment"]["sum"], 25000)  # products + delivery: the check

    def test_paid_on_receipt_keeps_poster_prices(self):
        self.push(self.order())
        body = self.poster.created()[0]
        self.assertNotIn("payment", body)
        self.assertNotIn("price", body["products"][0])
        self.assertNotIn("Оплачено переводом", body["comment"])


class CheckoutTests(OrderFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            pickup_pay_transfer=True, delivery_pay_transfer=True,
            transfer_payment_details=REQUISITES,
        )
        self.settings.refresh_from_db()

    def test_the_transfer_is_offered_where_the_register_takes_the_order(self):
        self.assertEqual(payment_modes_for(self.settings, Order.Channel.PICKUP),
                         (PREPAID, ON_RECEIPT))
        self.assertEqual(payment_modes_for(self.settings, Order.Channel.DELIVERY),
                         (PREPAID, ON_RECEIPT))

    def test_still_with_the_requisites_only(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(transfer_payment_details="")
        self.settings.refresh_from_db()
        self.assertEqual(payment_modes_for(self.settings, Order.Channel.PICKUP), (ON_RECEIPT,))

    def test_staff_are_told_the_register_confirms_the_transfer(self):
        self._cart()
        with self.captureOnCommitCallbacks(execute=True):
            order = place_remote_order(
                partner_id=self.partner.id, telegram_id=TG, slot="now", use_bonuses=False,
                expected_total="400.00", expected_to_pay="400.00", payment=PREPAID, now=NOON,
            )
        self.assertEqual(PosterOrderPush.objects.get(order=order).status, Push.SENT)
        self.assertEqual(self.poster.created()[0]["payment"]["sum"], 40000)
        note = StaffNotification.objects.get(order=order).message
        self.assertIn("Гость сообщил о переводе 400.00 грн", note)
        self.assertIn("прежде чем принять заказ на кассе", note)
        self.assertNotIn("подтвердите оплату в карточке заказа", note)


class PrepaidFixture(StatusFixture):
    """2 × «Бургер» paid in advance by transfer (400 грн), sent to Poster:
    online order 11, check 12."""

    def place(self, *, quantity=2):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            pickup_pay_transfer=True, transfer_payment_details=REQUISITES,
        )
        self._cart(quantity)
        with self.captureOnCommitCallbacks(execute=True):
            return place_remote_order(
                partner_id=self.partner.id, telegram_id=TG, slot="now", use_bonuses=False,
                expected_total=f"{200 * quantity}.00", expected_to_pay=f"{200 * quantity}.00",
                payment=PREPAID, now=NOON,
            )

    def payments(self):
        return list(Payment.objects.filter(bill_id=self.bill_id, status=Payment.Status.PAID))

    def returned(self):
        """Staff messages to return the transfer."""

        return list(StaffNotification.objects.filter(title__contains="верните перевод")
                    .values_list("message", flat=True))


class AcceptedTests(PrepaidFixture):
    def test_accepting_at_the_register_confirms_the_transfer(self):
        BonusProgram.objects.create(
            partner=self.partner, name="Cashback",
            program_type=BonusProgram.ProgramType.CASHBACK, percent="5.00",
        )
        self.register(online_status=1, check_status=1)
        self.assertEqual(self.sync(), "accepted")
        order = self.fresh()
        self.assertEqual(order.status, Order.Status.ACCEPTED)
        self.assertIsNotNone(order.paid_at)
        [payment] = self.payments()
        self.assertEqual(
            (payment.method, payment.amount, payment.provider_code, payment.external_payment_id,
             payment.created_by),
            (Payment.Method.TRANSFER, Decimal("400.00"), "poster", "12", None),
        )
        self.assertIn("Poster", payment.comment)
        self.assertEqual(self.bill().status, Bill.Status.PAID)
        # No Botera cashback over Poster (owner 2026-11-24).
        self.assertFalse(BonusTransaction.objects.filter(guest=self.guest).exists())
        history = OrderStatusHistory.objects.get(order=order, to_status=Order.Status.ACCEPTED)
        self.assertIsNone(history.changed_by)
        self.assertIn("Poster", history.note)
        self.assertEqual(self.guest_told.call_args.kwargs["to_status"], Order.Status.ACCEPTED)
        self.assertEqual((self.push_.attention, self.attention_messages()), ("", []))

    def test_accepted_once(self):
        self.register(online_status=1, check_status=1)
        self.sync()
        self.assertEqual(self.sync(), "")
        self.assertEqual(len(self.payments()), 1)
        self.assertEqual(OrderStatusHistory.objects.filter(
            order=self.order_, to_status=Order.Status.ACCEPTED).count(), 1)

    def test_new_at_the_register_waits(self):
        self.register(online_status=0, check_status=1)
        self.sync()
        self.assertEqual(self.fresh().status, Order.Status.NEW)
        self.assertEqual(self.payments(), [])


class ClosedTests(PrepaidFixture):
    def close(self, total="40000"):
        # Spike P1: a fully prepaid check closes as «mixed» with the money of
        # the application (``payed_third_party``), not the register's.
        self.register(online_status=1, check_status=2, pay_type=3, total=total, paid=total)

    def test_the_closed_check_completes_it(self):
        self.register(online_status=1, check_status=1)
        self.sync()
        self.close()
        self.assertEqual(self.sync(), "paid")
        self.assertEqual(self.fresh().status, Order.Status.COMPLETED)
        [payment] = self.payments()  # the transfer, not a second payment by the register
        self.assertEqual(payment.method, Payment.Method.TRANSFER)
        self.assertEqual((self.push_.attention, self.attention_messages()), ("", []))

    def test_closed_without_the_accept_webhook(self):
        self.close()
        self.sync()
        order = self.fresh()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        [payment] = self.payments()
        self.assertEqual((payment.method, payment.amount), (Payment.Method.TRANSFER,
                                                            Decimal("400.00")))

    def test_another_sum_is_marked_for_admin(self):
        self.close(total="39000")
        self.sync()
        self.assertEqual(self.fresh().status, Order.Status.COMPLETED)
        self.assertIn("390.00", self.push_.attention)
        self.assertEqual(self.attention_messages(), [])  # Admin's, not staff's


class RejectedTests(PrepaidFixture):
    def test_rejected_at_the_register(self):
        self.register(online_status=7, check_status=4, total="40000", paid="0")
        self.assertEqual(self.sync(), "canceled")
        self.assertEqual(self.fresh().status, Order.Status.CANCELED)
        self.assertEqual(self.bill().status, Bill.Status.CANCELED)
        self.assertEqual(self.payments(), [])
        [message] = self.returned()
        self.assertIn("верните", message)
        self.assertIn("переводом", self.push_.attention)
        self.assertEqual(self.guest_told.call_args.kwargs["to_status"], Order.Status.CANCELED)
        self.assertFalse(register_holds(self.order_))  # the register is done with it

    def test_a_rejected_online_order_or_check_alone_lets_go_too(self):
        for online_status, check_status in ((7, 1), (0, 4)):
            with self.subTest(online=online_status, check=check_status):
                self.register(online_status=online_status, check_status=check_status)
                self.sync()
                self.assertFalse(register_holds(self.fresh()))

    def test_rejected_once(self):
        self.register(online_status=7, check_status=4)
        self.sync()
        self.sync()
        self.assertEqual(len(self.returned()), 1)

    def test_the_guest_hears_the_venue_returns_the_money(self):
        self.register(online_status=7, check_status=4)
        self.sync()
        order = self.fresh()
        with texts("ru"):
            self.assertIn("вернёт", _remote_status_note(order, Order.Status.CANCELED))
            # Never at the register: staff said the money did not come.
            PosterOrderPush.objects.filter(pk=self.push_.pk).update(status=Push.FAILED)
            self.assertIn("Оплата не поступила", _remote_status_note(order, Order.Status.CANCELED))

    def test_so_does_one_whose_check_was_removed(self):
        self.register(online_status=0, check_status=3)
        self.sync()
        with texts("ru"):
            self.assertIn("вернёт", _remote_status_note(self.fresh(), Order.Status.CANCELED))

    def test_a_check_removed_before_accepting_cancels_it_too(self):
        self.register(online_status=0, check_status=3, total="40000", paid="0")
        self.assertEqual(self.sync(), "canceled")
        self.assertEqual(self.fresh().status, Order.Status.CANCELED)
        self.assertEqual(len(self.returned()), 1)


class RemovedAfterPaymentTests(PrepaidFixture):
    """The transfer recorded, then the check deleted: the register returns
    nothing (spike D3) — staff return the transfer and record it."""

    def test_accepted_then_removed(self):
        self.register(online_status=1, check_status=1)
        self.sync()
        self.register(online_status=1, check_status=3, total="40000", paid="0")
        self.assertEqual(self.sync(), "attention")
        self.assertEqual(self.fresh().status, Order.Status.ACCEPTED)
        self.assertEqual(self.bill().status, Bill.Status.PAID)
        self.assertFalse(Refund.objects.filter(bill_id=self.bill_id).exists())  # not «На кассе»
        self.assertIn("переводом", self.push_.attention)
        [message] = self.returned()
        self.assertIn("возврат", message)

    def test_completed_then_removed(self):
        self.register(online_status=1, check_status=2, pay_type=3, total="40000", paid="40000")
        self.sync()
        self.register(online_status=1, check_status=3, pay_type=3, total="40000", paid="40000")
        self.sync()
        self.assertEqual(self.fresh().status, Order.Status.COMPLETED)
        self.assertFalse(Refund.objects.filter(bill_id=self.bill_id).exists())
        self.assertEqual(len(self.returned()), 1)


class StaffWhileHeldTests(PrepaidFixture):
    def test_staff_cannot_confirm_or_reject_the_transfer(self):
        for action in (confirm_transfer, reject_transfer):
            with self.subTest(action=action.__name__):
                with self.assertRaisesMessage(RemoteOrderError, "касса Poster"):
                    action(order=self.order_, actor=self.cashier)
        self.assertEqual(self.fresh().status, Order.Status.NEW)
        self.assertEqual(self.payments(), [])

    def test_a_failed_push_is_botera_again(self):
        PosterOrderPush.objects.filter(pk=self.push_.pk).update(status=Push.FAILED)
        confirm_transfer(order=self.order_, actor=self.cashier)
        self.assertEqual(self.fresh().status, Order.Status.ACCEPTED)

    def test_the_timer_leaves_it_to_the_register(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(transfer_wait_minutes=15)
        later = self.order_.created_at + timedelta(hours=1)
        self.assertEqual(cancel_expired_transfer_orders(now=later), 0)
        self.assertEqual(self.fresh().status, Order.Status.NEW)
        # Poster off: the order is Botera's again, and so is the timer.
        PosterConnection.objects.filter(partner=self.partner).update(
            status=PosterConnection.Status.DISCONNECTED)
        self.assertEqual(cancel_expired_transfer_orders(now=later), 1)
        self.assertEqual(self.fresh().status, Order.Status.CANCELED)


class TransferRefundTests(PrepaidFixture):
    """The register confirmed the transfer; the register gives none of it
    back (spike D3): staff return it and record it in Botera (owner
    2026-11-24) — the card, the Mini App, Admin and the service let them
    once the guest has the order or the register undid it."""

    def setUp(self):
        super().setUp()
        self.register(online_status=1, check_status=1)
        self.sync()
        self.owner = User.objects.create_user(
            username="prepaid_refund_owner", password="x", partner=self.partner,
            role=User.Role.OWNER, is_staff=True, language="ru",
        )

    def offered(self):
        order = OrderRepository.by_public_id_for_partner(self.partner.id, self.order_.public_id)
        card = bool([item for item in _data(_order_actions_keyboard(order))
                     if item.startswith("strf:")])
        mini_app = _order_json(Order.objects.get(pk=order.pk), now=timezone.now())["can_refund"]
        request = RequestFactory().get("/admin/")
        request.user, request.session = self.owner, {}
        admin_page = "refund" in [action.code for action in BillAdmin(
            Bill, admin.site).get_object_actions(request, self.bill())]
        return card, mini_app, admin_page

    def refund(self, lines=None):
        bill = self.bill()
        return refund_bill_lines(
            bill=bill, bill_item_ids=lines or [line.id for line in bill.items.all()],
            actor=self.owner, reason_code=Refund.ReasonCode.OTHER, method=Refund.Method.TRANSFER,
        )

    def test_the_card_says_the_register_confirms_the_transfer(self):
        order = OrderRepository.by_public_id_for_partner(self.partner.id, self.order_.public_id)
        self.assertIn("это и подтверждение перевода", _render_order_card(order))

    def test_not_while_the_register_has_the_order(self):
        self.assertEqual(self.offered(), (False, False, False))
        with self.assertRaisesMessage(BillingServiceError, "на кассе Poster"):
            self.refund()

    def test_after_the_register_undid_it(self):
        self.register(online_status=1, check_status=3)
        self.sync()
        self.assertFalse(register_holds(self.fresh()))  # the order is Botera's again
        self.assertEqual(self.offered(), (True, True, True))
        staff = SimpleNamespace(partner=self.partner, employee=SimpleNamespace(user=self.owner))
        self.assertEqual(_refund_order(staff, self.order_.public_id)[0].pk, self.order_.pk)
        refund = self.refund()
        self.assertEqual((refund.method, refund.money_amount),
                         (Refund.Method.TRANSFER, Decimal("400.00")))
        self.assertEqual(self.fresh().status, Order.Status.CANCELED)

    def test_once_the_guest_has_it(self):
        self.register(online_status=1, check_status=2, pay_type=3, paid="40000")
        self.sync()
        self.assertTrue(register_holds(self.fresh()))
        self.assertEqual(self.offered(), (True, True, True))
        self.refund()
        self.assertEqual(self.fresh().status, Order.Status.COMPLETED)  # as any completed order


@skipUnless(db_connection.vendor == "postgresql", "нужны построчные блокировки")
class TwoAcceptsAtOnceTests(PosterReadMixin, OrderSetup, TransactionTestCase):
    """The online order's and the check's webhooks a second apart: the
    transfer is recorded once."""

    def test_the_transfer_recorded_once(self):
        from tests.apps.integrations.poster.test_statuses import TwoEventsAtOnceTests

        mock.patch("apps.orders.services.send_guest_order_status_update").start()
        self.addCleanup(mock.patch.stopall)
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            pickup_pay_transfer=True, transfer_payment_details=REQUISITES,
        )
        self._cart()
        self.poster.answers["incomingOrders.createIncomingOrder"] = dict(CREATED)
        order = place_remote_order(
            partner_id=self.partner.id, telegram_id=TG, slot="now", use_bonuses=False,
            expected_total="400.00", expected_to_pay="400.00", payment=PREPAID, now=NOON,
        )
        push = PosterOrderPush.objects.get(order=order)
        self.poster.answers["incomingOrders.getIncomingOrder"] = online(1)
        self.poster.answers["dash.getTransaction"] = check(1)
        TwoEventsAtOnceTests._two_at_once(self, push)
        self.assertEqual(Payment.objects.filter(bill__bill_orders__order=order,
                                                status=Payment.Status.PAID).count(), 1)
        self.assertEqual(OrderStatusHistory.objects.filter(
            order=order, to_status=Order.Status.ACCEPTED).count(), 1)
