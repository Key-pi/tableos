"""A bill paid in equal parts on the staff screen (PQ-009, step 1).

The bill card offers «➗ Поровну»; split, it shows each part with «Наличные»
and «Карта»; a paid part shows how it was paid. The service decides
(``apps/billing/bill_split.py``)."""

from apps.billing.models import Bill, Payment
from tests.webapp.test_staff_api import WAITER_TG, BillsFixture

CASHIER = 7700204


class StaffBillSplitTests(BillsFixture):
    def setUp(self):
        super().setUp()
        visit = self.visit()
        self.staff_call("visit_action", {"visit": visit["id"], "action": "shared"},
                        user_id=CASHIER)
        self.bill_id = self.staff_call("bills", user_id=CASHIER)[1]["bills"][0]["id"]

    def card(self):
        return self.staff_call("bill", user_id=CASHIER, query=f"?id={self.bill_id}")[1]["bill"]

    def pay(self, part, method="cash", amount="40.00"):
        return self.staff_call("bill_split_pay", {"bill": self.bill_id, "part": part,
                                                  "method": method, "amount": amount},
                               user_id=CASHIER)

    def test_two_parts_by_cash_and_card(self):
        card = self.card()
        self.assertEqual((card["can_split"], card["split"]), (True, []))
        status, body = self.staff_call("bill_split", {"bill": self.bill_id, "parts": 2},
                                       user_id=CASHIER)
        self.assertEqual(status, 200, body)
        self.assertEqual([(part["number"], part["amount"], part["paid"])
                          for part in body["bill"]["split"]],
                         [(1, "40.00", False), (2, "40.00", False)])
        status, body = self.pay(1)
        self.assertEqual(status, 200, body)
        first = body["bill"]["split"][0]
        self.assertEqual((first["paid"], first["method"]), (True, "Готівка"))
        self.assertFalse(body["bill"]["can_split"])
        self.assertEqual(self.pay(1)[0], 400)  # already paid
        self.assertEqual(self.staff_call("bill_split", {"bill": self.bill_id, "parts": 0},
                                         user_id=CASHIER)[0], 400)
        self.assertEqual(self.pay(2, "terminal", amount="45.00")[0], 400)  # stale amount
        status, body = self.pay(2, "terminal")
        self.assertEqual((status, body["bill"]["status"]), (200, "paid"))
        bill = Bill.objects.get(public_id=self.bill_id)
        self.assertEqual(sorted(bill.payments.values_list("split_part", "method")),
                         [(1, Payment.Method.CASH), (2, Payment.Method.TERMINAL)])

    def test_undo_before_a_part_is_paid(self):
        self.staff_call("bill_split", {"bill": self.bill_id, "parts": 3}, user_id=CASHIER)
        status, body = self.staff_call("bill_split", {"bill": self.bill_id, "parts": 0},
                                       user_id=CASHIER)
        self.assertEqual((status, body["bill"]["split"]), (200, []))

    def test_bad_requests_and_a_waiter(self):
        self.staff_call("bill_split", {"bill": self.bill_id, "parts": 2}, user_id=CASHIER)
        self.assertEqual(self.pay("1")[0], 400)
        self.assertEqual(self.pay(1, method="bonuses")[0], 400)
        self.assertEqual(self.staff_call("bill_split", {"bill": self.bill_id, "parts": "x"},
                                         user_id=CASHIER)[0], 400)
        self.assertEqual(self.staff_call("bill_split", {"bill": self.bill_id, "parts": 2},
                                         user_id=WAITER_TG)[0], 403)
        self.assertEqual(self.staff_call("bill_split_pay", {"bill": self.bill_id, "part": 1,
                                                            "method": "cash"},
                                         user_id=WAITER_TG)[0], 403)
        self.assertFalse(Payment.objects.exists())
