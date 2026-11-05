"""Mini App for staff (PQ-048, stage 2): open orders and calls, the same actions
as the bot's work mode through the same services."""

from decimal import Decimal

from django.urls import reverse

from apps.billing.staff_actions import staff_order_actions
from apps.employees.models import EmployeeProfile
from apps.notifications.models import GuestCall
from apps.orders.models import Order
from apps.partners.models import PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import GuestProfile, TelegramAccount, User
from bot.handlers.staff import _order_actions_keyboard
from webapp.tests_guest_api import TG, MiniAppFixture, make_venue

OWNER_TG, WAITER_TG = 7700201, 7700202


def staff(partner, username, role, telegram_id):
    user = User.objects.create_user(
        username=username, password="x", partner=partner, role=role, first_name=username
    )
    return EmployeeProfile.objects.create(
        partner=partner, user=user,
        telegram_account=TelegramAccount.objects.get_or_create(telegram_id=telegram_id)[0],
    )


class StaffFixture(MiniAppFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            pickup_pay_on_receipt=True, delivery_pay_on_receipt=True
        )
        self.owner = staff(self.partner, "kava_owner", User.Role.OWNER, OWNER_TG)
        self.waiter = staff(self.partner, "kava_waiter", User.Role.WAITER, WAITER_TG)

    def place(self, channel="pickup", payment="prepaid", **details):
        self.choose(channel)
        self.add_latte()
        self.assertEqual(
            self.call("details", {"phone": "+380501112233", **details})[0], 200
        )
        quote = self.call("checkout")[1]
        status, body = self.call("place", {
            "slot": "now", "payment": payment, "shipping_cost": quote.get("shipping_cost", ""),
            "expected_total": quote["total"], "expected_to_pay": quote["to_pay"],
        })
        self.assertEqual(status, 200, body)
        return Order.objects.get(public_id=body["order"])

    def staff_call(self, name, data=None, *, user_id=OWNER_TG, query=""):
        return self.call(f"staff_{name}", data, user_id=user_id, query=query)

    def act(self, order, action, user_id=OWNER_TG, **extra):
        return self.staff_call(
            "action", {"order": order.public_id, "action": action, **extra}, user_id=user_id
        )


class StaffAccessTests(StaffFixture):
    def test_only_staff_of_this_venue_open_it(self):
        status, body = self.staff_call("state", user_id=TG)  # a guest
        self.assertEqual((status, body["code"]), (403, "not_staff"))
        self.waiter.user.is_active = False
        self.waiter.user.save()
        self.assertEqual(self.staff_call("state", user_id=WAITER_TG)[0], 403)
        self.assertEqual(self.staff_call("state", user_id=OWNER_TG)[0], 200)

    def test_the_module_is_checked(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(module_webapp_enabled=False)
        self.assertEqual(self.staff_call("state")[1]["code"], "module_off")

    def test_the_page(self):
        response = self.client.get(reverse("webapp:staff", args=[self.bot.id.hex]))
        self.assertContains(response, "staff.js")
        self.assertNotIn("X-Frame-Options", response)


class StaffOrdersTests(StaffFixture):
    def test_open_orders_of_this_venue_with_their_actions(self):
        order = self.place()
        other = make_venue("Other")
        foreign = Order.objects.create(
            partner=other, guest=GuestProfile.objects.create(
                partner=other, telegram_account=TelegramAccount.objects.get(telegram_id=TG)
            ), channel="pickup", total_amount=Decimal("10.00"),
        )
        status, body = self.staff_call("state")
        self.assertEqual(status, 200, body)
        self.assertEqual([item["id"] for item in body["orders"]], [order.public_id])
        card = body["orders"][0]
        self.assertEqual([action["code"] for action in card["actions"]],
                         ["transfer_ok", "transfer_no"])
        self.assertIn("80", card["payment"])
        self.assertEqual(card["phone"], "+380501112233")
        # Another venue's order is not found even by its number.
        status, _body = self.act(foreign, "status:accepted")
        self.assertEqual(status, 404)

    def test_transfer_then_statuses_to_the_end(self):
        order = self.place()
        self.assertTrue(self.act(order, "transfer_ok")[1]["ok"])
        order.refresh_from_db()
        self.assertEqual((order.status, order.paid_at is not None), ("accepted", True))
        for action, expected in (("status:ready", "ready"), ("status:completed", "completed")):
            status, body = self.act(order, action, user_id=WAITER_TG)
            self.assertEqual(status, 200, body)
            order.refresh_from_db()
            self.assertEqual(order.status, expected)
        # Paid and completed: no longer open.
        self.assertEqual(self.staff_call("state")[1]["orders"], [])

    def test_the_role_is_checked_by_the_service(self):
        order = self.place()
        status, body = self.act(order, "transfer_ok", user_id=WAITER_TG)
        self.assertEqual(status, 400)
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)

    def test_an_out_of_date_card_does_nothing(self):
        order = self.place(payment="on_receipt")
        self.assertTrue(self.act(order, "status:accepted")[1]["ok"])
        status, body = self.act(order, "status:accepted")
        self.assertEqual(status, 200)
        self.assertTrue(body["stale"])
        self.assertEqual(body["order"]["status"], "accepted")
        self.assertEqual(self.act(order, "fly")[1]["stale"], True)

    def test_payment_on_receipt_and_the_refusal(self):
        order = self.place(payment="on_receipt")
        codes = [action["code"] for action in self.staff_call("state")[1]["orders"][0]["actions"]]
        self.assertEqual(codes, ["receipt_cash", "receipt_card", "status:accepted", "receipt_no"])
        self.assertTrue(self.act(order, "receipt_cash")[1]["ok"])
        order.refresh_from_db()
        self.assertIsNotNone(order.paid_at)
        second = self.place(payment="on_receipt")
        self.assertTrue(self.act(second, "receipt_no")[1]["ok"])
        second.refresh_from_db()
        self.assertEqual(second.status, "canceled")

    def test_shipping_needs_the_tracking_number(self):
        order = self.place(channel="shipping", payment="prepaid", carrier="nova_poshta",
                           destination="Київ, відділення 3")
        self.act(order, "transfer_ok")
        self.act(order, "status:ready")
        order.refresh_from_db()
        self.assertIn("ship", staff_order_actions(order))
        status, body = self.act(order, "ship", tracking="")
        self.assertEqual(status, 400)
        status, body = self.act(order, "ship", tracking="20450000000000")
        self.assertEqual(status, 200, body)
        order.refresh_from_db()
        self.assertEqual((order.status, order.tracking_number), ("delivering", "20450000000000"))


class SameButtonsAsTheBotTests(StaffFixture):
    """The Mini App offers exactly what the bot's order card offers."""

    BOT_CODES = {
        "stafftxok": "transfer_ok", "stafftxno": "transfer_no", "staffrccash": "receipt_cash",
        "staffrccard": "receipt_card", "staffrccod": "receipt_cod", "staffrcno": "receipt_no",
        "staffship": "ship",
    }

    def bot_actions(self, order):
        markup = _order_actions_keyboard(order)
        codes = []
        for row in markup.inline_keyboard:
            for button in row:
                data = button.callback_data or ""
                head = data.split(":", 1)[0]
                if head == "stafforder":
                    codes.append("status:" + data.split(":")[2])
                elif head in self.BOT_CODES:
                    codes.append(self.BOT_CODES[head])
        return codes

    def test_for_every_kind_of_order(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(remote_unpaid_orders_limit=0)
        cases = [
            self.place(),
            self.place(payment="on_receipt"),
            self.place(channel="delivery", payment="on_receipt", address="Хрещатик 1"),
        ]
        cases.append(self.place(channel="shipping", carrier="nova_poshta", destination="Київ 3"))
        for order in cases:
            for _step in range(4):
                order = Order.objects.get(id=order.id)
                self.assertEqual(staff_order_actions(order), self.bot_actions(order), order.channel)
                forward = [code for code in staff_order_actions(order)
                           if code not in ("transfer_no", "receipt_no", "status:canceled")]
                if not forward:
                    break
                self.act(order, forward[0], tracking="20450000000000")


class StaffCallsTests(StaffFixture):
    def test_the_first_one_goes(self):
        from apps.notifications.services import request_staff_assistance

        table = Table.objects.create(partner=self.partner, number=4)
        activate_table_session(
            partner_id=self.partner.id, telegram_id=TG, payload=table.deep_link_payload
        )
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_staff_call_enabled=True
        )
        self.waiter.notify_on_guest_calls = True
        self.waiter.save()
        second = staff(self.partner, "kava_waiter2", User.Role.WAITER, 7700203)
        second.notify_on_guest_calls = True
        second.save()
        request_staff_assistance(
            partner_id=self.partner.id, telegram_id=TG, call_target="waiter",
            call_target_label="Официант",
        )
        calls = self.staff_call("state", user_id=WAITER_TG)[1]["calls"]
        self.assertEqual([call["table"] for call in calls], [4])
        late = self.staff_call("state", user_id=7700203)[1]["calls"]
        self.assertEqual(len(late), 1)
        status, body = self.staff_call("call", {"call": calls[0]["id"]}, user_id=WAITER_TG)
        self.assertEqual((status, body["table"]), (200, 4))
        self.assertEqual(GuestCall.objects.get().taken_by, self.waiter)
        # The other waiter: the call is gone from the list and cannot be taken.
        self.assertEqual(self.staff_call("state", user_id=7700203)[1]["calls"], [])
        status, body = self.staff_call("call", {"call": late[0]["id"]}, user_id=7700203)
        self.assertEqual(status, 400)
        self.assertIn("kava_waiter", body["error"])
