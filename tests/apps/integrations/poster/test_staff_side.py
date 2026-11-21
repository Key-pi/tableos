"""Staff and an order that went to Poster (PQ-058, step 7, owner 2026-11-21):
«Новый заказ» stays with a line about Poster; in the bot, the Mini App and
Admin staff only say «Готово» and «В пути» — accepting, cancelling, the
money and completing are the register's, and the services refuse them too.
An order that did not reach Poster, or a venue without Poster — as before."""

from decimal import Decimal
from types import SimpleNamespace

from django.contrib import admin
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.billing.admin import BillAdmin
from apps.billing.models import Bill, Payment, Refund
from apps.billing.remote_orders import (
    RemoteOrderError,
    cancel_unpaid_remote_order,
    place_remote_order,
    record_payment_on_receipt,
)
from apps.billing.services import BillingServiceError, cancel_order, refund_bill_lines
from apps.billing.staff_actions import STATUS, staff_order_actions
from apps.integrations.poster.models import PosterConnection, PosterOrderPush
from apps.integrations.poster.selectors import goes_to_register, register_holds
from apps.menu.models import MenuItem
from apps.notifications.models import StaffNotification
from apps.orders.admin import order_admin_actions
from apps.orders.models import Order
from apps.orders.repositories import OrderRepository
from apps.orders.selectors import get_open_orders_for_partner
from apps.orders.services import (
    OrderFlowError,
    add_items_to_cart_for_telegram_user,
    transition_order_status,
)
from apps.users.models import User
from bot.handlers.staff import (
    _build_order_shortcuts,
    _notification_order_action,
    _order_actions_keyboard,
    _render_order_card,
)
from tests.apps.billing.test_remote_orders import NOON, TG
from tests.apps.integrations.poster.test_orders import OrderFixture
from tests.apps.integrations.poster.test_statuses import StatusFixture
from tests.bot.test_guest_flow import _data
from webapp.context import WebAppDenied
from webapp.staff_billing import _refund_order
from webapp.staff_views import _order_json

Push = PosterOrderPush.Status
Status = Order.Status
HOLDS = "Этот заказ ведёт касса Poster"


def disconnect(partner):
    PosterConnection.objects.filter(partner=partner).update(
        status=PosterConnection.Status.DISCONNECTED,
    )


def new_order_message(order):
    return StaffNotification.objects.get(
        order=order, category=StaffNotification.Category.ORDER_CREATED,
    ).message


class NewOrderMessageTests(OrderFixture):
    def test_it_says_the_order_goes_to_poster(self):
        order = self.place()

        message = new_order_message(order)

        self.assertIn("Заказ уходит в Poster — принимайте его на кассе.", message)
        self.assertIn("Оплата при получении: 400.00 грн — её примут на кассе Poster.", message)
        self.assertNotIn("Отметьте оплату", message)
        self.assertTrue(goes_to_register(order))

    def test_an_order_with_own_items_stays_in_botera_with_the_old_message(self):
        PosterConnection.objects.filter(pk=self.connection.pk).update(
            menu_mode=PosterConnection.MenuMode.WITH_OWN,
        )
        MenuItem.objects.create(partner=self.partner, category=self.item.category,
                                public_id="OWN00001", name="Морс", price="50.00")
        self._cart(1)
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, raw_items="OWN00001x1",
        )
        with self.captureOnCommitCallbacks(execute=True):
            order = place_remote_order(
                partner_id=self.partner.id, telegram_id=TG, slot="now", use_bonuses=False,
                expected_total="250.00", expected_to_pay="250.00",
                payment=Order.PaymentTiming.ON_RECEIPT, now=NOON,
            )

        message = new_order_message(order)
        self.assertNotIn("Poster", message)
        self.assertIn("Отметьте оплату", message)
        self.assertFalse(goes_to_register(order))

    def test_without_poster_as_before(self):
        disconnect(self.partner)

        message = new_order_message(self.place())

        self.assertNotIn("Poster", message)
        self.assertIn("Отметьте оплату", message)


class StaffButtonsTests(StatusFixture):
    def codes(self, order=None):
        order = Order.objects.get(pk=(order or self.order_).pk)
        return staff_order_actions(order)

    def bot_buttons(self, order=None):
        order = Order.objects.get(pk=(order or self.order_).pk)
        return [data for data in _data(_order_actions_keyboard(order))
                if not data.startswith(("stafforders:", "stafforderrefresh:"))]

    def set_status(self, status):
        Order.objects.filter(pk=self.order_.pk).update(status=status)

    def test_a_new_order_in_poster_is_accepted_at_the_register_not_in_the_bot(self):
        self.assertTrue(register_holds(self.order_))
        self.assertEqual(self.codes(), [])
        self.assertEqual(self.bot_buttons(), [])
        notification = StaffNotification.objects.get(
            order=self.order_, category=StaffNotification.Category.ORDER_CREATED,
        )
        self.assertEqual(_notification_order_action(notification), "")
        [(_code, _label, action)] = _build_order_shortcuts([self.order_])
        self.assertEqual(action, "")

    def test_accepted_at_the_register_staff_say_it_is_ready(self):
        self.register(online_status=1)
        self.sync()
        self.assertEqual(Order.objects.get(pk=self.order_.pk).status, Status.ACCEPTED)

        self.assertEqual(self.codes(), [STATUS + Status.READY])
        self.assertEqual(self.bot_buttons(), [f"stafforder:{self.order_.public_id}:ready"])
        order = Order.objects.get(pk=self.order_.pk)
        self.assertEqual([action.code for action in order_admin_actions(order)],
                         [STATUS + Status.READY])

    def test_a_ready_pickup_waits_for_the_check(self):
        self.set_status(Status.READY)

        self.assertEqual(self.codes(), [])
        self.assertEqual(self.bot_buttons(), [])

    def test_a_delivery_goes_on_the_way_and_waits_for_the_check(self):
        Order.objects.filter(pk=self.order_.pk).update(channel=Order.Channel.DELIVERY)
        self.set_status(Status.READY)
        self.assertEqual(self.codes(), [STATUS + Status.DELIVERING])

        self.set_status(Status.DELIVERING)
        self.assertEqual(self.codes(), [])
        self.assertEqual(self.bot_buttons(), [])

    def test_staff_do_not_accept_cancel_take_the_money_or_complete(self):
        with self.assertRaisesMessage(OrderFlowError, HOLDS):
            transition_order_status(order=self.order_, to_status=Status.ACCEPTED,
                                    actor_user=self.cashier)
        with self.assertRaisesMessage(BillingServiceError, HOLDS):
            cancel_order(order=self.order_, actor=self.cashier)
        with self.assertRaisesMessage(RemoteOrderError, HOLDS):
            cancel_unpaid_remote_order(order=self.order_, actor=self.cashier)
        self.set_status(Status.READY)
        with self.assertRaisesMessage(RemoteOrderError, HOLDS):
            record_payment_on_receipt(order=self.order_, actor=self.cashier,
                                      method=Payment.Method.CASH)
        with self.assertRaisesMessage(OrderFlowError, HOLDS):
            transition_order_status(order=self.order_, to_status=Status.COMPLETED,
                                    actor_user=self.cashier)
        order = Order.objects.get(pk=self.order_.pk)
        self.assertEqual((order.status, order.paid_at), (Status.READY, None))

    def test_staff_say_ready_and_the_register_closes_it(self):
        self.register(online_status=1)
        self.sync()
        transition_order_status(order=self.order_, to_status=Status.READY,
                                actor_user=self.cashier)

        self.register(online_status=1, check_status=2, pay_type=1, paid="40000")
        self.sync()

        order = Order.objects.get(pk=self.order_.pk)
        self.assertEqual(order.status, Status.COMPLETED)
        self.assertIsNotNone(order.paid_at)

    def test_an_order_that_did_not_reach_poster_has_every_button(self):
        PosterOrderPush.objects.filter(pk=self.push_.pk).update(status=Push.FAILED)

        self.assertFalse(register_holds(self.order_))
        self.assertIn(STATUS + Status.ACCEPTED, self.codes())
        self.assertIn("receipt_cash", self.codes())
        transition_order_status(order=self.order_, to_status=Status.ACCEPTED,
                                actor_user=self.cashier)

    def test_without_poster_every_button_comes_back(self):
        disconnect(self.partner)

        self.assertFalse(register_holds(self.order_))
        self.assertIn(f"stafforder:{self.order_.public_id}:accepted", self.bot_buttons())
        self.set_status(Status.READY)
        self.assertIn("receipt_cash", self.codes())


    def test_lists_mark_the_orders_in_one_query(self):
        order = get_open_orders_for_partner(self.partner.id).get(pk=self.order_.pk)
        with self.assertNumQueries(0):
            self.assertTrue(register_holds(order))
        PosterOrderPush.objects.filter(pk=self.push_.pk).update(status=Push.FAILED)
        order = get_open_orders_for_partner(self.partner.id).get(pk=self.order_.pk)
        self.assertFalse(register_holds(order))
        disconnect(self.partner)
        PosterOrderPush.objects.filter(pk=self.push_.pk).update(status=Push.SENT)
        order = get_open_orders_for_partner(self.partner.id).get(pk=self.order_.pk)
        self.assertFalse(register_holds(order))


class OrdersOfOtherVenuesTests(TestCase):
    def test_an_order_without_a_push_is_not_held(self):
        from apps.partners.models import Partner
        from apps.users.models import GuestProfile

        partner = Partner.objects.create(name="Other", slug="other-venue",
                                         status=Partner.Status.ACTIVE)
        order = Order.objects.create(partner=partner, guest=GuestProfile.objects.create(
            partner=partner), channel=Order.Channel.PICKUP)

        self.assertFalse(register_holds(order))
        self.assertFalse(goes_to_register(order))


class RefundTests(StatusFixture):
    """The register took the money, so it gives it back (owner 2026-11-21)."""

    def setUp(self):
        super().setUp()
        self.register(online_status=1, check_status=2, pay_type=1, paid="40000")
        self.sync()
        self.owner = User.objects.create_user(
            username="poster_refund_owner", password="x", partner=self.partner,
            role=User.Role.OWNER, is_staff=True, language="ru",
        )
        self.bill = Bill.objects.get(pk=self.bill_id)
        self.assertEqual(self.bill.status, Bill.Status.PAID)

    def refund(self):
        return refund_bill_lines(
            bill=self.bill, bill_item_ids=[line.id for line in self.bill.items.all()],
            actor=self.owner, reason_code=Refund.ReasonCode.OTHER, method=Refund.Method.CASH,
        )

    def card(self):
        order = OrderRepository.by_public_id_for_partner(self.partner.id, self.order_.public_id)
        return _render_order_card(order), _data(_order_actions_keyboard(order))

    def admin_codes(self):
        request = RequestFactory().get("/admin/")
        request.user, request.session = self.owner, {}
        page = BillAdmin(Bill, admin.site)
        return [action.code for action in page.get_object_actions(request, self.bill)]

    def test_the_card_sends_refunds_to_the_register(self):
        text, data = self.card()

        self.assertIn("Заказ ведёт касса Poster", text)
        self.assertFalse([item for item in data if item.startswith("strf:")])
        order = Order.objects.get(pk=self.order_.pk)
        self.assertFalse(_order_json(order, now=timezone.now())["can_refund"])
        self.assertNotIn("refund", self.admin_codes())

    def test_the_mini_app_refund_page_sends_to_the_register(self):
        staff = SimpleNamespace(partner=self.partner, employee=SimpleNamespace(user=self.owner))

        with self.assertRaises(WebAppDenied) as caught:
            _refund_order(staff, self.order_.public_id)
        self.assertIn("на кассе Poster", str(caught.exception.message))

    def test_the_service_refuses_a_refund(self):
        with self.assertRaisesMessage(BillingServiceError, "на кассе Poster"):
            self.refund()
        self.assertFalse(Refund.objects.exists())

    def test_without_poster_botera_refunds_as_before(self):
        disconnect(self.partner)

        text, data = self.card()

        self.assertNotIn("касса Poster", text)
        self.assertIn(f"strf:{self.order_.public_id}:0", data)
        self.assertIn("refund", self.admin_codes())
        self.assertEqual(self.refund().money_amount, Decimal("400.00"))
