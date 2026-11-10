"""Buttons of a bill in Admin (admin step 3, owner 2026-11-10, PQ-050).

The bot's bill card (``staff_bill_actions``, ``bill_change_rights``): issue
and payments in one press; removing lines, voiding and refunds go through
their reason forms (PQ-028, PQ-037). A billed order is cancelled the same
way."""

from decimal import Decimal

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Bill
from apps.billing.services import create_bill_from_orders
from apps.orders.services import create_order_from_session
from apps.users.constants import AdminAccessPreset
from apps.users.models import User
from tests.apps.billing import test_billing as billing_tests
from tests.apps.orders.test_order_admin_actions import admin_access


class BillAdminFixture(TestCase):
    setUp_billing = billing_tests.BillingServiceTests.setUp

    def setUp(self):
        self.setUp_billing()
        self.owner = User.objects.create_user(
            username="bill_owner", password="x", partner=self.partner, role=User.Role.OWNER
        )
        admin_access(self.owner, AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(self.owner)

    def new_order(self):
        return create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[{
                "menu_item_id": self.menu_item.id, "item_name": self.menu_item.name,
                "unit_price": self.menu_item.price, "quantity": 2,
            }],
        )

    def bill(self):
        return create_bill_from_orders(partner_id=self.partner.id, order_ids=[self.new_order().id])

    def actions(self, bill):
        response = self.client.get(reverse("admin:billing_bill_change", args=[bill.pk]))
        self.assertEqual(response.status_code, 200)
        return {action.code: action for action in response.context["object_actions"]}

    def press(self, bill, code):
        return self.client.post(
            reverse("admin:billing_bill_act", args=[bill.pk]), {"object_action": code}
        )


class BillButtonsTests(BillAdminFixture):
    def test_issue_then_take_the_money(self):
        bill = self.bill()
        actions = self.actions(bill)
        self.assertEqual(list(actions)[:4], ["issue", "pay_bonus", "pay_cash", "pay_terminal"])
        self.assertEqual(actions["issue"].style, "primary")
        self.assertEqual(str(actions["pay_cash"].label), "Оплачено наличными: 300,00 грн")
        self.assertTrue(actions["pay_bonus"].confirm)
        self.assertEqual(actions["void"].list_action, "void_selected_bills")
        self.assertIn("bill__id__exact=", actions["lines"].url)
        self.press(bill, "issue")
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.ISSUED)
        self.press(bill, "pay_cash")
        bill.refresh_from_db()
        self.assertEqual((bill.status, bill.paid_amount), (Bill.Status.PAID, Decimal("300.00")))
        # Paid: no payments, voiding or line changes; a refund by its lines.
        self.assertEqual(list(self.actions(bill)), ["refund"])

    def test_a_press_on_a_paid_bill_does_nothing(self):
        bill = self.bill()
        self.press(bill, "pay_terminal")
        response = self.press(bill, "pay_cash")
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn(
            "Это действие уже недоступно — страница устарела. Посмотрите, что изменилось.",
            messages,
        )
        bill.refresh_from_db()
        self.assertEqual(bill.paid_amount, Decimal("300.00"))

    def test_voiding_asks_for_the_reason(self):
        bill = self.bill()
        response = self.client.post(reverse("admin:billing_bill_changelist"), {
            "action": "void_selected_bills", "_selected_action": str(bill.pk), "index": "0",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Аннулировать счета")
        self.assertContains(response, 'name="reason_code"')
        bill.refresh_from_db()
        self.assertNotEqual(bill.status, Bill.Status.CANCELED)

    def test_without_the_owners_right_no_changes_or_voiding(self):
        manager = User.objects.create_user(
            username="bill_manager", password="x", partner=self.partner, role=User.Role.MANAGER
        )
        admin_access(manager, AdminAccessPreset.MANAGER)
        self.client.force_login(manager)
        bill = self.bill()
        actions = self.actions(bill)
        self.assertIn("pay_cash", actions)
        self.assertNotIn("void", actions)
        self.assertNotIn("lines", actions)
        self.press(bill, "pay_cash")
        self.assertEqual(list(self.actions(bill)), [])  # no refunds either


class BilledOrderCancelTests(BillAdminFixture):
    def test_a_billed_order_is_cancelled_with_a_reason(self):
        bill = self.bill()
        order = bill.items.first().order
        response = self.client.get(reverse("admin:orders_order_change", args=[order.pk]))
        cancel = next(action for action in response.context["object_actions"]
                      if action.code == "status:canceled")
        self.assertEqual(cancel.list_action, "mark_as_canceled")
        response = self.client.post(reverse("admin:orders_order_changelist"), {
            "action": "mark_as_canceled", "_selected_action": str(order.pk), "index": "0",
        })
        self.assertContains(response, 'name="reason_code"')

    def test_an_order_not_billed_is_cancelled_in_one_press(self):
        order = self.new_order()
        response = self.client.get(reverse("admin:orders_order_change", args=[order.pk]))
        cancel = next(action for action in response.context["object_actions"]
                      if action.code == "status:canceled")
        self.assertEqual((cancel.list_action, cancel.style), ("", "danger"))
        self.assertTrue(cancel.confirm)
        self.client.post(reverse("admin:orders_order_act", args=[order.pk]),
                         {"object_action": "status:canceled"})
        order.refresh_from_db()
        self.assertEqual(order.status, "canceled")
