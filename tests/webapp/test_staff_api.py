"""Mini App for staff (PQ-048, stage 2): open orders and calls, the same actions
as the bot's work mode through the same services."""

from decimal import Decimal

from django.test import TestCase as DjangoTestCase
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
from tests.webapp.test_guest_api import TG, MiniAppFixture, make_venue

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
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_staff_screen_enabled=False
        )
        self.assertEqual(self.staff_call("state")[1]["code"], "module_off")

    def test_its_own_module_without_the_guests_mini_app_or_the_cart(self):
        """PQ-033: the staff screen is a module of its own (owner, 2026-11-06)."""

        order = self.place()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_webapp_enabled=False, module_cart_enabled=False
        )
        status, body = self.staff_call("state")
        self.assertEqual(status, 200, body)
        self.assertEqual([card["id"] for card in body["orders"]], [order.public_id])
        self.assertEqual(self.act(order, "accepted")[0], 200)

    def test_switching_it_changes_nothing_for_guests(self):
        guest_before = self.call("state")
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_staff_screen_enabled=False
        )
        guest_after = self.call("state")
        self.assertEqual(guest_before, guest_after)

    def test_the_page(self):
        response = self.client.get(reverse("webapp:staff", args=[self.bot.id.hex]))
        self.assertContains(response, "staff.js?v=")
        self.assertContains(response, "staff.css?v=")
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


class BillsFixture(StaffFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(module_billing_enabled=True)
        self.cashier = staff(self.partner, "kava_cashier", User.Role.CASHIER, 7700204)
        self.table = Table.objects.create(partner=self.partner, number=7)
        activate_table_session(
            partner_id=self.partner.id, telegram_id=TG, payload=self.table.deep_link_payload
        )
        self.add_latte()
        self.assertEqual(self.call("place", {})[0], 200)  # an order to the table

    def visit(self, user_id=7700204):
        status, body = self.staff_call("tables", user_id=user_id)
        self.assertEqual(status, 200, body)
        self.assertEqual([table["number"] for table in body["tables"]], [7])
        return body["tables"][0]["visits"][0]


class StaffBillsAndTablesTests(BillsFixture):
    def test_a_waiter_has_no_bills_or_tables(self):
        state = self.staff_call("state", user_id=WAITER_TG)[1]
        self.assertEqual(
            {key: state["rights"][key] for key in ("billing", "tables")},
            {"billing": False, "tables": False},
        )
        for name in ("bills", "tables"):
            status, body = self.staff_call(name, user_id=WAITER_TG)
            self.assertEqual((status, body["code"]), (403, "no_right"), name)
        status, _body = self.staff_call(
            "visit_action", {"visit": "00" * 16, "action": "close"}, user_id=WAITER_TG
        )
        self.assertEqual(status, 403)

    def test_bill_the_visit_take_the_money_close_the_visit(self):
        visit = self.visit()
        self.assertEqual((visit["unbilled"], visit["can_bill"], len(visit["guests"])), (1, True, 1))
        status, body = self.staff_call(
            "visit_action", {"visit": visit["id"], "action": "shared"}, user_id=7700204
        )
        self.assertEqual(status, 200, body)
        bills = self.staff_call("bills", user_id=7700204)[1]["bills"]
        self.assertEqual([bill["total"] for bill in bills], ["80.00"])
        card = self.staff_call("bill", user_id=7700204, query=f"?id={bills[0]['id']}")[1]["bill"]
        self.assertEqual([action["code"] for action in card["actions"]],
                         ["issue", "pay_cash", "pay_terminal"])
        status, body = self.staff_call(
            "bill_action", {"bill": card["id"], "action": "pay_cash"}, user_id=7700204
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["bill"]["status"], "paid")
        # Paid: the same button again does nothing.
        status, body = self.staff_call(
            "bill_action", {"bill": card["id"], "action": "pay_cash"}, user_id=7700204
        )
        self.assertTrue(body["stale"])
        # Paid but not served yet: the visit does not close (the service says why).
        status, body = self.staff_call(
            "visit_action", {"visit": visit["id"], "action": "close"}, user_id=7700204
        )
        self.assertEqual((status, body["code"]), (400, "refused"))
        order = Order.objects.get(table=self.table)
        for action in ("status:accepted", "status:ready", "status:completed"):
            self.assertTrue(self.act(order, action)[1]["ok"], action)
        tables = self.staff_call("tables", user_id=7700204)[1]["tables"]
        if tables:  # unless it closed itself after payment (PQ-027)
            status, body = self.staff_call(
                "visit_action", {"visit": visit["id"], "action": "close"}, user_id=7700204
            )
            self.assertEqual(status, 200, body)
        self.assertEqual(self.staff_call("tables", user_id=7700204)[1]["tables"], [])

    def test_a_bill_request_from_the_table(self):
        from apps.billing.table_bills import create_billing_request_for_telegram_user

        create_billing_request_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, request_type="shared"
        )
        requests = self.staff_call("bills", user_id=7700204)[1]["requests"]
        self.assertEqual([item["table"] for item in requests], [7])
        status, _body = self.staff_call(
            "request_action", {"request": requests[0]["id"], "action": "cancel"}, user_id=7700204
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.staff_call("bills", user_id=7700204)[1]["requests"], [])

    def test_a_new_bill_request_is_counted_for_the_tab_badge(self):
        from apps.billing.table_bills import create_billing_request_for_telegram_user

        self.assertEqual(self.staff_call("state", user_id=7700204)[1]["bill_requests"], [])
        create_billing_request_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, request_type="shared"
        )
        requests = self.staff_call("state", user_id=7700204)[1]["bill_requests"]
        self.assertEqual(len(requests), 1)
        # Without the right to bills the screen is not told about them.
        self.assertEqual(self.staff_call("state", user_id=WAITER_TG)[1]["bill_requests"], [])

    def test_another_venues_bill_or_visit_is_not_found(self):
        other = make_venue("Other")
        self.assertEqual(self.staff_call("bill", query="?id=nope")[0], 404)
        from apps.tables.models import TableVisit

        foreign_table = Table.objects.create(partner=other, number=1)
        foreign_visit = TableVisit.objects.create(partner=other, table=foreign_table)
        status, _body = self.staff_call(
            "visit_action", {"visit": foreign_visit.id.hex, "action": "close"}, user_id=7700204
        )
        self.assertEqual(status, 404)

    def test_same_bill_buttons_as_the_bot(self):
        from apps.billing.repositories import BillRepository
        from apps.billing.staff_actions import staff_bill_actions
        from bot.handlers.staff import _build_bill_card_message

        visit = self.visit()
        self.staff_call("visit_action", {"visit": visit["id"], "action": "shared"},
                        user_id=7700204)
        bill = BillRepository.open_for_partner(self.partner.id).get()
        codes = {"staffbillissue": "issue", "staffbillpaybonus": "pay_bonus",
                 "staffbillpaycash": "pay_cash", "staffbillpayterminal": "pay_terminal"}
        for _step in range(2):
            _text, markup = _build_bill_card_message(bill, self.cashier.user)
            bot_codes = [
                codes[button.callback_data.split(":")[0]]
                for row in markup.inline_keyboard for button in row
                if button.callback_data.split(":")[0] in codes
            ]
            self.assertEqual(staff_bill_actions(bill), bot_codes)
            self.staff_call("bill_action", {"bill": bill.public_id, "action": "issue"},
                            user_id=7700204)
            bill = BillRepository.all_for_partner(self.partner.id).get(id=bill.id)


class StaffBillChangesAndRefundsTests(BillsFixture):
    """Changing, voiding and refunding as in the bot (PQ-028, PQ-037)."""

    def bill(self):
        visit = self.visit()
        self.staff_call("visit_action", {"visit": visit["id"], "action": "shared"},
                        user_id=7700204)
        bill_id = self.staff_call("bills", user_id=7700204)[1]["bills"][0]["id"]
        return self.staff_call("bill", query=f"?id={bill_id}")[1]["bill"]  # the owner

    def test_only_with_the_right_from_the_owner(self):
        self.bill()
        bill_id = self.staff_call("bills", user_id=7700204)[1]["bills"][0]["id"]
        card = self.staff_call("bill", user_id=7700204, query=f"?id={bill_id}")[1]["bill"]
        self.assertEqual((card["can_edit"], card["can_void"]), (False, False))
        status, body = self.staff_call(
            "bill_change", {"bill": bill_id, "action": "void", "reason": "other"},
            user_id=7700204,
        )
        self.assertEqual(status, 400)  # the service refuses: no right

    def test_the_owner_removes_a_line_then_voids(self):
        card = self.bill()
        self.assertEqual((card["can_edit"], card["can_void"]), (True, True))
        self.assertEqual(self.staff_call(
            "bill_change", {"bill": card["id"], "action": "void", "reason": ""}
        )[0], 400)  # a reason is required
        status, body = self.staff_call("bill_change", {
            "bill": card["id"], "action": "void", "reason": "guest_refused", "comment": "ушли",
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(body["bill"]["status"], "canceled")
        from apps.billing.models import BillAdjustment

        adjustment = BillAdjustment.objects.get(bill__public_id=card["id"])
        self.assertEqual(
            (adjustment.reason_code, adjustment.reason_text), ("guest_refused", "ушли")
        )

    def test_remove_a_line(self):
        self.add_latte()
        self.call("place", {})
        card = self.bill()
        self.assertEqual(len(card["lines"]), 2)
        status, body = self.staff_call("bill_change", {
            "bill": card["id"], "action": "remove_line", "line": card["lines"][0]["id"],
            "reason": "input_error",
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["bill"]["lines"]), 1)
        again = self.staff_call("bill_change", {
            "bill": card["id"], "action": "remove_line", "line": card["lines"][0]["id"],
            "reason": "input_error",
        })
        self.assertEqual(again[0], 400)

    def test_refund_a_paid_order(self):
        card = self.bill()
        self.staff_call("bill_action", {"bill": card["id"], "action": "pay_cash"},
                        user_id=7700204)
        order = Order.objects.get(table=self.table)
        found = self.staff_call("order", query=f"?id=%23{order.public_id.upper()}")[1]["order"]
        self.assertTrue(found["can_refund"])
        refund = self.staff_call("refund", query=f"?order={order.public_id}")
        self.assertEqual(refund[0], 200, refund[1])
        lines = [line["id"] for line in refund[1]["lines"]]
        preview = self.staff_call("refund_preview", {"order": order.public_id, "lines": lines})[1]
        self.assertEqual(preview["money"], "80.00")
        status, body = self.staff_call("refund_do", {
            "order": order.public_id, "lines": lines, "reason": "quality", "method": "cash",
        })
        self.assertEqual(status, 200, body)
        refund = self.staff_call("refund", query=f"?order={order.public_id}")[1]
        self.assertTrue(all(line["refunded"] for line in refund["lines"]))
        # The same lines again: nothing to refund.
        self.assertEqual(self.staff_call("refund_do", {
            "order": order.public_id, "lines": lines, "reason": "quality", "method": "cash",
        })[0], 400)
        # A cashier without the owner's right cannot.
        self.assertEqual(
            self.staff_call("refund", user_id=7700204, query=f"?order={order.public_id}")[0], 403
        )


class StaffSaleAndReportTests(StaffFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_quick_sale_enabled=True, module_reports_enabled=True
        )
        self.cashier = staff(self.partner, "kava_cashier", User.Role.CASHIER, 7700204)

    def test_a_quick_sale_with_the_guest_code(self):
        menu = self.staff_call("sale_menu", user_id=7700204)[1]
        latte = next(item for item in menu["categories"][0]["items"] if item["name"] == "Лате")
        guest = GuestProfile.objects.get_or_create(
            partner=self.partner, telegram_account=TelegramAccount.objects.get_or_create(
                telegram_id=TG)[0],
        )[0]
        body = {"customer_code": guest.customer_code.lower(), "items": {latte["id"]: 2}}
        status, preview = self.staff_call("sale_preview", body, user_id=7700204)
        self.assertEqual(status, 200, preview)
        self.assertEqual((preview["sale"]["total"], preview["sale"]["items"][0]["quantity"]),
                         ("160.00", 2))
        self.assertTrue(preview["sale"]["guest"])
        status, done = self.staff_call("sale_confirm", body, user_id=7700204)
        self.assertEqual(status, 200, done)
        from apps.bonuses.models import WalkInSale

        sale = WalkInSale.objects.get()
        self.assertEqual((sale.amount, sale.guest_id, sale.created_by), (Decimal("160.00"),
                         guest.id, self.cashier.user))

    def _sale_with_bonuses(self):
        from apps.bonuses.models import BonusProgram, BonusTransaction

        BonusProgram.objects.create(
            partner=self.partner, name="5%",
            trigger_event=BonusProgram.TriggerEvent.MANUAL_PURCHASE,
            program_type=BonusProgram.ProgramType.CASHBACK, percent=Decimal("5"),
            max_redeem_share=Decimal("50"), is_active=True,
        )
        guest = GuestProfile.objects.get_or_create(
            partner=self.partner, telegram_account=TelegramAccount.objects.get_or_create(
                telegram_id=TG)[0],
        )[0]
        BonusTransaction.objects.create(
            partner=self.partner, guest=guest, transaction_type="manual",
            amount=Decimal("30.00"), source_type="test", source_id="seed",
            idempotency_key=f"seed:{guest.id}",
        )
        from apps.bonuses.services import sync_bonus_balance_from_ledger

        sync_bonus_balance_from_ledger(guest)
        menu = self.staff_call("sale_menu", user_id=7700204)[1]
        latte = next(item for item in menu["categories"][0]["items"] if item["name"] == "Лате")
        return guest, {"customer_code": guest.customer_code, "items": {latte["id"]: 2},
                       "redeem": True}

    def test_a_repeated_sale_is_saved_once(self):
        """A slow line: the screen gave up waiting, the cashier presses again —
        the same key, the same sale; no second accrual or spend (PQ-048)."""

        from apps.bonuses.models import BonusTransaction, WalkInSale

        guest, body = self._sale_with_bonuses()
        body["key"] = "6f1c2a4e-0b9d-4c55-9d3a-8a1f0f7e2b10"
        first = self.staff_call("sale_confirm", body, user_id=7700204)
        again = self.staff_call("sale_confirm", body, user_id=7700204)
        self.assertEqual((first[0], again[0]), (200, 200), (first, again))
        self.assertEqual(WalkInSale.objects.count(), 1)
        sale = WalkInSale.objects.get()
        self.assertGreater(sale.bonus_spent_amount, 0)
        self.assertEqual(
            BonusTransaction.objects.filter(walk_in_sale=sale).count(), 2  # spend + accrual
        )
        self.assertIn(str(sale.amount), again[1]["notice"])
        # A new sale has a new key.
        body["key"] = "0a7d3b51-55e2-4a3e-b1a6-2c4f9e8d7c61"
        self.assertEqual(self.staff_call("sale_confirm", body, user_id=7700204)[0], 200)
        self.assertEqual(WalkInSale.objects.count(), 2)

    def test_a_broken_key_is_refused(self):
        _guest, body = self._sale_with_bonuses()
        body["key"] = "x" * 80
        self.assertEqual(self.staff_call("sale_confirm", body, user_id=7700204)[0], 400)

    def test_unknown_code_empty_sale_and_a_waiter_are_refused(self):
        self.assertEqual(self.staff_call(
            "sale_preview", {"customer_code": "NOPE00", "items": {}}, user_id=7700204
        )[0], 400)
        self.assertEqual(self.staff_call(
            "sale_confirm", {"customer_code": "", "items": {}}, user_id=7700204
        )[0], 400)
        self.assertEqual(self.staff_call("sale_menu", user_id=WAITER_TG)[0], 403)
        self.assertEqual(self.staff_call("report", user_id=WAITER_TG)[0], 403)

    def test_the_day_report(self):
        order = self.place(payment="on_receipt")
        self.act(order, "receipt_cash")
        status, report = self.staff_call("report", user_id=7700204)
        self.assertEqual(status, 200, report)
        self.assertEqual(report["money"][0]["value"], "80.00")
        self.assertEqual(self.staff_call("state", user_id=7700204)[1]["rights"],
                         {"billing": False, "tables": True, "sale": True, "report": True,
                          "invite": False})


class StaffInviteTests(StaffFixture):
    def test_the_owner_invites_a_waiter_is_refused(self):
        roles = self.staff_call("invite_roles")[1]["roles"]
        self.assertIn("waiter", [role["code"] for role in roles])
        status, body = self.staff_call("invite", {"role": "waiter"})
        self.assertEqual(status, 200, body)
        self.assertIn("https://t.me/kava_bot?start=inv_", body["link"])
        self.assertTrue(body["share"].startswith("https://t.me/share/url?url="))
        self.assertEqual(self.staff_call("invite", {"role": "owner"})[0], 400)
        self.assertEqual(self.staff_call("invite_roles", user_id=WAITER_TG)[0], 403)
        self.assertFalse(self.staff_call("state", user_id=WAITER_TG)[1]["rights"]["invite"])


class StaffStateVersionTests(StaffFixture):
    """While nothing changed the screen gets a short answer (load, 2026-11-05)."""

    def test_unchanged_until_something_happens(self):
        first = self.staff_call("state")[1]
        version = first["version"]
        quiet = self.staff_call("state", query=f"?since={version}")[1]
        self.assertEqual(quiet, {"unchanged": True, "version": version})
        order = self.place()  # a new order
        fresh = self.staff_call("state", query=f"?since={version}")[1]
        self.assertNotIn("unchanged", fresh)
        self.assertEqual(len(fresh["orders"]), 1)
        self.assertIn("created", fresh["orders"][0])
        again = self.staff_call("state", query=f"?since={fresh['version']}")[1]
        self.assertTrue(again["unchanged"])
        self.act(order, "transfer_ok")  # a status change
        changed = self.staff_call("state", query=f"?since={fresh['version']}")[1]
        self.assertEqual(changed["orders"][0]["status"], "accepted")

    def test_the_short_answer_stays_cheap(self):
        """Each open screen asks every 8 s: the short answer is a handful of
        queries (bot, person, language, settings, calls, bill requests,
        orders), with the person and the module still checked each time."""

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        version = self.staff_call("state")[1]["version"]
        with CaptureQueriesContext(connection) as queries:
            quiet = self.staff_call("state", query=f"?since={version}")[1]
        self.assertTrue(quiet["unchanged"])
        self.assertLessEqual(len(queries), 8, [q["sql"][:120] for q in queries])

    def test_each_person_has_their_own_version(self):
        version = self.staff_call("state")[1]["version"]
        other = self.staff_call("state", user_id=WAITER_TG, query=f"?since={version}")[1]
        self.assertNotIn("unchanged", other)  # other rights, other screen


class StaffScreenMigrationTests(DjangoTestCase):
    """partners.0025: where the staff screen worked (the guests' Mini App with
    the cart), it stays on as its own module; nowhere else."""

    def test_only_where_it_worked(self):
        import importlib

        from django.apps import apps

        migration = importlib.import_module("apps.partners.migrations.0025_staff_screen_module")
        make_venue("Both", staff_screen=False)
        no_cart = make_venue("No cart", staff_screen=False)
        PartnerBotSettings.objects.filter(partner=no_cart).update(module_cart_enabled=False)
        off = make_venue("Off", webapp=False, staff_screen=False)
        migration.keep_staff_screens(apps, None)
        on = set(
            PartnerBotSettings.objects.filter(module_staff_screen_enabled=True)
            .values_list("partner__name", flat=True)
        )
        self.assertEqual(on, {"Both"})
        self.assertFalse(
            PartnerBotSettings.objects.get(partner=off).module_staff_screen_enabled
        )
