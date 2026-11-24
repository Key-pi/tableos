"""Orders from the bot to the Poster register (PQ-058, step 5): what is sent
and in which units, what is not sent and the staff message, repeats without
a second online order, recovery, and that Poster never breaks an order.
Poster is replaced by ``FakePoster``; the answers copy the trial account of
step 0 (docs/architecture/POSTER.md, «Итоги шага 0»)."""

import json
import threading
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest import mock, skipUnless

from django.db import connection as db_connection
from django.db import transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.billing.remote_orders import place_remote_order, save_guest_phone
from apps.employees.models import EmployeeProfile
from apps.integrations.poster import orders, tasks
from apps.integrations.poster.client import PosterError
from apps.integrations.poster.models import (
    PosterConnection,
    PosterOrderPush,
    PosterRef,
    PosterWebhookEvent,
)
from apps.menu.models import MenuItemVariant, Modifier, ModifierGroup
from apps.notifications.models import StaffNotification
from apps.orders.models import Order, OrderItem
from apps.partners.models import PartnerBotSettings
from apps.users.models import TelegramAccount, User
from tests.apps.billing.test_remote_orders import NOON, TG, PickupFixtureMixin
from tests.apps.integrations.poster.test_menu_sync import connect

Push = PosterOrderPush.Status
Kind = PosterRef.Kind
CREATED = {"incoming_order_id": 11, "transaction_id": 12, "status": 0, "spot_id": 1}


class FakePoster:
    """``PosterClient`` with answers per method: a value, an exception, a
    list of them (one per call) or a function of ``(params, body)``."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def __call__(self, token, **kwargs):
        self.token = token
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def call(self, method, *, params=None, body=None):
        self.calls.append((method, params, body))
        answer = self.answers[method]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if callable(answer) and not isinstance(answer, Exception):
            answer = answer(params, body)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def created(self):
        return [body for method, _params, body in self.calls
                if method == "incomingOrders.createIncomingOrder"]


def temporary(message="Poster did not answer in time"):
    return PosterError(message, temporary=True)


class OrderSetup(PickupFixtureMixin):
    """A pickup venue on Poster: «Бургер» is product 1 of the trial account."""

    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            pickup_pay_transfer=False, pickup_pay_on_receipt=True,
            module_delivery_enabled=True, delivery_pay_transfer=False,
            delivery_pay_on_receipt=True, delivery_fee="50.00",
        )
        save_guest_phone(partner_id=self.partner.id, telegram_id=TG, phone="+380 50 111-22-33")
        # Texts read in Russian below: the venue's language and the cashier's.
        PartnerBotSettings.objects.filter(id=self.settings.id).update(default_language="ru")
        User.objects.filter(pk=self.cashier.pk).update(language="ru")
        self.connection = connect(self.partner)
        PosterRef.objects.create(partner=self.partner, kind=Kind.PRODUCT, poster_id="1",
                                 object_id=self.item.pk)
        self.poster = FakePoster(**{"incomingOrders.createIncomingOrder": dict(CREATED)})
        patcher = mock.patch.object(orders, "PosterClient", self.poster)
        patcher.start()
        self.addCleanup(patcher.stop)

    def order(self, *, channel=Order.Channel.PICKUP, payment=Order.PaymentTiming.ON_RECEIPT,
              requested_for=None, lines=(), fee=None, **fields):
        """An order made directly, for what one line or one field sends."""

        order = Order.objects.create(
            partner=self.partner, guest=self.guest, channel=channel, payment_timing=payment,
            requested_for=requested_for, total_amount="200.00", **fields,
        )
        for line in lines or [{}]:
            OrderItem.objects.create(
                partner=self.partner, order=order, menu_item=line.get("item", self.item),
                item_name=line.get("name", "Бургер"), unit_price="200.00",
                quantity=line.get("quantity", 1), variant=line.get("variant"),
                modifiers=line.get("modifiers", []),
            )
        if fee is not None:
            OrderItem.objects.create(
                partner=self.partner, order=order, kind=OrderItem.Kind.DELIVERY_FEE,
                item_name="Доставка", unit_price=fee, quantity=1,
            )
        return order

    def staff_messages(self):
        return list(StaffNotification.objects.filter(title__contains="Poster").values_list(
            "title", "message"))


class OrderFixture(OrderSetup, TestCase):
    def place(self, *, quantity=2):
        self._cart(quantity)
        with self.captureOnCommitCallbacks(execute=True):
            return place_remote_order(
                partner_id=self.partner.id, telegram_id=TG, slot="now", use_bonuses=False,
                expected_total=f"{200 * quantity}.00", expected_to_pay=f"{200 * quantity}.00",
                payment=Order.PaymentTiming.ON_RECEIPT, now=NOON,
            )

    def push(self, order):
        """Queue and send, as the signal after checkout does."""

        with self.captureOnCommitCallbacks(execute=True):
            orders.queue_order(order.pk)
        return PosterOrderPush.objects.get(order=order)


class WhatIsSentTests(OrderFixture):
    def test_a_placed_order_goes_to_poster(self):
        order = self.place()
        push = PosterOrderPush.objects.get(order=order)
        self.assertEqual((push.status, push.incoming_order_id, push.transaction_id, push.attempts),
                         (Push.SENT, 11, 12, 1))
        self.assertIsNotNone(push.sent_at)
        [body] = self.poster.created()
        self.assertEqual(body["spot_id"], "1")
        self.assertEqual(body["phone"], "+380501112233")
        self.assertEqual(body["service_mode"], "2")  # «З собою»
        self.assertEqual(body["products"], [{"product_id": "1", "count": 2}])
        self.assertIn(f"Botera #{order.public_id}", body["comment"])
        self.assertNotIn("payment", body)
        self.assertEqual(self.poster.token, "100200:token-placeholder")

    def test_the_guests_comment_and_time(self):
        order = self.order(comment="Без цибулі", requested_for=NOON + timedelta(minutes=30))
        self.push(order)
        comment = self.poster.created()[0]["comment"]
        self.assertIn("Без цибулі", comment)
        self.assertIn("12:30", comment)  # Kyiv summer time of NOON + 30 minutes

    def test_a_variant_and_add_ons(self):
        latte = MenuItemVariant.objects.create(item=self.item, name="M 350 мл", price="65.00")
        PosterRef.objects.create(partner=self.partner, kind=Kind.VARIANT, poster_id="1:2",
                                 object_id=latte.pk)
        sauce = ModifierGroup.objects.create(partner=self.partner, name="Соус")
        cream = Modifier.objects.create(group=sauce, name="Сметана", price="15.00")
        served = ModifierGroup.objects.create(partner=self.partner, name="Подача")
        plate = Modifier.objects.create(group=served, name="На тарілці")
        PosterRef.objects.create(partner=self.partner, kind=Kind.MODIFIER, poster_id="1:1",
                                 object_id=cream.pk)
        PosterRef.objects.create(partner=self.partner, kind=Kind.MODIFIER, poster_id="2:3",
                                 object_id=plate.pk)
        self.push(self.order(lines=[{
            "variant": latte, "quantity": 2,
            "modifiers": [{"id": cream.id.hex, "name": "Сметана", "price": "15.00"},
                          {"id": plate.id.hex, "name": "На тарілці", "price": "0.00"}],
        }]))
        [line] = self.poster.created()[0]["products"]
        self.assertEqual((line["product_id"], line["modificator_id"], line["count"]),
                         ("1", "2", 2))
        self.assertEqual(json.loads(line["modification"]), [{"m": 1, "a": 1}, {"m": 3, "a": 1}])

    def test_delivery(self):
        order = self.order(channel=Order.Channel.DELIVERY, delivery_address="вул. Тестова, 1",
                           requested_for=datetime(2026, 11, 20, 16, 30, tzinfo=dt_timezone.utc),
                           fee="50.00")
        self.push(order)
        body = self.poster.created()[0]
        self.assertEqual(body["service_mode"], "3")
        self.assertEqual(body["address"], "вул. Тестова, 1")
        self.assertEqual(body["delivery_price"], 5000)  # kopecks, Poster adds it to the check
        self.assertEqual(body["delivery_time"], "2026-11-20 18:30:00")  # venue's local time
        self.assertEqual(body["products"], [{"product_id": "1", "count": 1}])  # no fee line

    def test_as_soon_as_possible_has_no_time(self):
        self.push(self.order(channel=Order.Channel.DELIVERY, delivery_address="вул. Тестова, 1"))
        self.assertNotIn("delivery_time", self.poster.created()[0])


class WhatIsNotQueuedTests(OrderFixture):
    def test_without_a_working_connection(self):
        for status in (PosterConnection.Status.DISCONNECTED, PosterConnection.Status.PENDING_SPOT):
            PosterConnection.objects.filter(pk=self.connection.pk).update(status=status)
            order = self.order()
            self.assertIsNone(orders.queue_order(order.pk))
        self.assertFalse(PosterOrderPush.objects.exists())

    def test_other_ways_of_ordering(self):
        for channel in (Order.Channel.TABLE, Order.Channel.SHIPPING, Order.Channel.COUNTER):
            self.assertIsNone(orders.queue_order(self.order(channel=channel).pk))
        self.assertFalse(PosterOrderPush.objects.exists())

    def test_queued_once(self):
        order = self.order()
        self.push(order)
        self.assertIsNone(orders.queue_order(order.pk))
        self.assertEqual(len(self.poster.created()), 1)


class RefusedTests(OrderFixture):
    """What Poster v1 cannot take is not sent: staff take it by hand."""

    def assert_refused(self, order, reason):
        push = self.push(order)
        self.assertEqual(push.status, Push.FAILED)
        self.assertIn(reason, push.last_error)
        self.assertEqual(self.poster.created(), [])
        [(title, message)] = self.staff_messages()
        self.assertIn(order.public_id, title)
        self.assertIn(reason, message)

    def test_paid_with_bonuses(self):
        self.assert_refused(self.order(bonus_spent="20.00"), "бонусами")

    def test_an_item_not_from_poster(self):
        own = self.item.__class__.objects.create(
            partner=self.partner, category=self.item.category, name="Лате", price="60.00",
        )
        self.assert_refused(self.order(lines=[{"item": own, "name": "Лате"}]), "Лате")

    def test_a_variant_not_from_poster(self):
        own = MenuItemVariant.objects.create(item=self.item, name="XL", price="90.00")
        self.assert_refused(self.order(lines=[{"variant": own, "name": "Бургер XL"}]),
                            "Бургер XL")

    def test_an_add_on_not_from_poster(self):
        group = ModifierGroup.objects.create(partner=self.partner, name="Сироп")
        syrup = Modifier.objects.create(group=group, name="Карамель", price="10.00")
        self.assert_refused(self.order(lines=[{"modifiers": [
            {"id": syrup.id.hex, "name": "Карамель", "price": "10.00"},
        ]}]), "Карамель")

    def test_poster_disconnected_before_sending(self):
        order = self.order()
        with self.captureOnCommitCallbacks(execute=False) as later:
            orders.queue_order(order.pk)
        PosterConnection.objects.filter(pk=self.connection.pk).update(
            status=PosterConnection.Status.DISCONNECTED,
        )
        with self.captureOnCommitCallbacks(execute=True):
            for callback in later:
                callback()
        push = PosterOrderPush.objects.get(order=order)
        self.assertEqual(push.status, Push.FAILED)
        self.assertEqual(self.poster.created(), [])

    def test_poster_refusing_is_final(self):
        cases = {155: "телефон", 210: "обязательн"}
        for code, reason in cases.items():
            with self.subTest(code=code):
                StaffNotification.objects.all().delete()
                self.poster.answers["incomingOrders.createIncomingOrder"] = PosterError(
                    "Poster refused", code=code, http_status=422)
                push = self.push(self.order())
                self.assertEqual(push.status, Push.FAILED)
                self.assertIn(reason, push.last_error)
                self.assertEqual(len(self.staff_messages()), 1)

    def test_the_message_reaches_staff_who_mute_new_orders(self):
        EmployeeProfile.objects.filter(partner=self.partner).update(notify_on_order_created=False)
        quiet = User.objects.create_user(username="pickup_off", password="x",
                                         partner=self.partner, role=User.Role.CASHIER)
        EmployeeProfile.objects.create(
            partner=self.partner, user=quiet, bot_notifications_enabled=True,
            telegram_account=TelegramAccount.objects.create(telegram_id=7300902),
            notify_channel_pickup=False,
        )
        self.push(self.order(bonus_spent="20.00"))
        self.assertEqual(
            list(StaffNotification.objects.filter(title__contains="Poster")
                 .values_list("employee__user__username", flat=True)),
            ["pickup_cashier"],  # the one who hears about pickup
        )


class RepeatTests(OrderFixture):
    """A timeout may hide an online order Poster made: it is looked for by the
    label among the inbox's ``incoming_order`` webhooks before trying again."""

    def setUp(self):
        super().setUp()
        self.later = mock.patch.object(orders, "_retry_later").start()

    def inbox(self, entity_id, *, account="kava"):
        body = {"account": account, "object": "incoming_order", "object_id": entity_id,
                "action": "added", "time": str(entity_id), "data": '{"type":1}'}
        PosterWebhookEvent.objects.create(
            dedupe_key=PosterWebhookEvent.dedupe_key_for(body), account=account,
            entity="incoming_order", entity_id=str(entity_id), action="added",
            event_time=entity_id, payload=body, status=PosterWebhookEvent.Status.IGNORED,
        )

    def test_a_timeout_waits_and_tries_again(self):
        self.poster.answers["incomingOrders.createIncomingOrder"] = [temporary(), dict(CREATED)]
        order = self.order()
        push = self.push(order)
        self.assertEqual((push.status, push.attempts), (Push.UNKNOWN, 1))
        self.later.assert_called_once()
        self.assertEqual(self.later.call_args.args, (str(push.pk), orders.RETRY_DELAYS[0]))
        orders.push_order(push.pk)
        push.refresh_from_db()
        self.assertEqual((push.status, push.attempts, push.incoming_order_id), (Push.SENT, 2, 11))
        self.assertEqual(len(self.poster.created()), 2)

    def test_an_order_poster_made_is_found_and_not_sent_again(self):
        self.poster.answers["incomingOrders.createIncomingOrder"] = [temporary()]
        order = self.order()
        push = self.push(order)
        self.inbox(11)
        self.inbox(12)  # newer, the same spot, another order
        found = {"incoming_order_id": 11, "transaction_id": 12, "spot_id": 1,
                 "comment": f"Самовывоз\nBotera #{order.public_id}"}
        other = {"incoming_order_id": 12, "transaction_id": 13, "spot_id": 1,
                 "comment": "Самовывоз\nBotera #00000000"}
        self.poster.answers["incomingOrders.getIncomingOrder"] = (
            lambda params, body: found if str(params["incoming_order_id"]) == "11" else other
        )
        orders.push_order(push.pk)
        push.refresh_from_db()
        self.assertEqual((push.status, push.incoming_order_id, push.transaction_id),
                         (Push.SENT, 11, 12))
        self.assertEqual(len(self.poster.created()), 1)

    def test_another_spot_or_account_is_not_ours(self):
        self.poster.answers["incomingOrders.createIncomingOrder"] = [temporary(), dict(CREATED)]
        order = self.order()
        push = self.push(order)
        self.inbox(11)
        self.inbox(12, account="other")
        self.poster.answers["incomingOrders.getIncomingOrder"] = lambda params, body: {
            "incoming_order_id": params["incoming_order_id"], "spot_id": 2,
            "comment": f"Botera #{order.public_id}",
        }
        orders.push_order(push.pk)
        push.refresh_from_db()
        self.assertEqual(push.status, Push.SENT)
        self.assertEqual(len(self.poster.created()), 2)
        looked = [params["incoming_order_id"] for method, params, _body in self.poster.calls
                  if method == "incomingOrders.getIncomingOrder"]
        self.assertEqual([str(entity_id) for entity_id in looked], ["11"])

    def test_after_the_last_attempt_staff_take_it(self):
        self.poster.answers["incomingOrders.createIncomingOrder"] = [
            temporary() for _attempt in range(orders.MAX_ATTEMPTS)
        ]
        push = self.push(self.order())
        for _attempt in range(orders.MAX_ATTEMPTS - 1):
            orders.push_order(push.pk)
        push.refresh_from_db()
        self.assertEqual((push.status, push.attempts), (Push.FAILED, orders.MAX_ATTEMPTS))
        self.assertEqual(len(self.staff_messages()), 1)
        self.assertEqual(len(self.poster.created()), orders.MAX_ATTEMPTS)

    def test_a_sent_order_is_not_sent_again(self):
        push = self.push(self.order())
        self.assertEqual(orders.push_order(push.pk), "")
        self.assertEqual(len(self.poster.created()), 1)


class RecoveryTests(OrderFixture):
    def test_stalled_pushes_are_taken_again(self):
        old = timezone.now() - timedelta(hours=1)
        waiting = PosterOrderPush.objects.create(partner=self.partner, order=self.order())
        stuck = PosterOrderPush.objects.create(partner=self.partner, order=self.order(),
                                               status=Push.SENDING, claimed_at=old, attempts=1)
        busy = PosterOrderPush.objects.create(partner=self.partner, order=self.order(),
                                              status=Push.SENDING, claimed_at=timezone.now(),
                                              attempts=1)
        PosterOrderPush.objects.filter(pk=waiting.pk).update(created_at=old)
        with mock.patch.object(tasks.push_order_task, "delay") as delay:
            self.assertEqual(orders.recover_pushes(), 2)
        self.assertEqual({call.args[0] for call in delay.call_args_list},
                         {str(waiting.pk), str(stuck.pk)})
        stuck.refresh_from_db()
        busy.refresh_from_db()
        self.assertEqual((stuck.status, busy.status), (Push.UNKNOWN, Push.SENDING))


class OrderIsNeverBrokenTests(OrderFixture):
    def test_a_failure_of_the_integration_does_not_break_checkout(self):
        with mock.patch.object(orders, "queue_order", side_effect=RuntimeError("boom")):
            order = self.place()
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())
        self.assertFalse(PosterOrderPush.objects.exists())

    def test_the_guest_and_staff_still_get_botera_messages(self):
        self.place()
        self.assertTrue(StaffNotification.objects.filter(title__startswith="Новый заказ").exists())

    def test_the_bill_does_not_change(self):
        order = self.place()
        order.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal("400.00"))
        self.assertIsNone(order.paid_at)


class NetworkOutsideTransactionTests(OrderSetup, TransactionTestCase):
    def test_poster_is_called_outside_a_transaction(self):
        seen = []

        def create(params, body):
            seen.append(transaction.get_connection().in_atomic_block)
            return dict(CREATED)

        self.poster.answers["incomingOrders.createIncomingOrder"] = create
        order = self.order()
        orders.queue_order(order.pk)  # outside a test transaction: on_commit runs at once
        self.assertEqual(seen, [False])
        self.assertEqual(PosterOrderPush.objects.get(order=order).status, Push.SENT)


@skipUnless(db_connection.vendor == "postgresql", "нужны построчные блокировки")
class TwoWorkersOnePushTests(OrderSetup, TransactionTestCase):
    """Celery may run the same push twice at once (a repeat, the recovery):
    the row's lock and status let one worker send it."""

    def test_sent_once(self):
        order = self.order()
        push = PosterOrderPush.objects.create(partner=self.partner, order=order)
        barrier = threading.Barrier(2)
        claimable = orders._claimable

        def claimable_and_wait(push_row):
            answer = claimable(push_row)
            try:
                barrier.wait(timeout=1.5)
            except threading.BrokenBarrierError:
                pass
            return answer

        errors = []

        def run():
            try:
                orders.push_order(push.pk)
            except Exception as exc:  # noqa: BLE001 — the test reports it
                errors.append(exc)
            finally:
                db_connection.close()

        with mock.patch.object(orders, "_claimable", side_effect=claimable_and_wait):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.poster.created()), 1)
        push.refresh_from_db()
        self.assertEqual(push.status, Push.SENT)
