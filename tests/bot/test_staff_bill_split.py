"""Equal parts in the staff bot (PQ-009 in the bot, step 1; owner 2026-11-20:
«Части поровну»): the bill card offers «➗ Поровну» on 2–8 parts; a split
bill lists its parts and each is paid by cash or terminal, as in the Mini
App; the whole-bill buttons give way to the parts."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.billing.bill_split import split_bill_equally
from apps.billing.models import Bill, BillSplitShare, Payment
from apps.billing.services import create_bill_from_orders
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.services import create_order_from_session
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import TelegramAccount, User
from bot.handlers import staff, staff_bill_split
from tests.modules import TABLE_VENUE_MODULES

CASHIER_TG, WAITER_TG, GUEST_TG = 7741001, 7741002, 7741009


class BotEqualPartsTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(name="Split Bot", slug="split-bot",
                                              status=Partner.Status.ACTIVE)
        PartnerBotSettings.objects.create(partner=self.partner, **TABLE_VENUE_MODULES,
                                          module_billing_enabled=True)
        table = Table.objects.create(partner=self.partner, number=4, qr_token="splitbot04")
        category = MenuCategory.objects.create(partner=self.partner, name="Bar")
        item = MenuItem.objects.create(partner=self.partner, category=category,
                                       name="Кальян & лёд", price="80.00")
        session = activate_table_session(partner_id=self.partner.id, telegram_id=GUEST_TG,
                                         payload=table.deep_link_payload)
        orders = [
            create_order_from_session(
                partner_id=self.partner.id, table_session=session,
                items=[{"menu_item_id": item.id, "item_name": item.name,
                        "unit_price": item.price, "quantity": 1}],
            )
            for _ in range(2)
        ]
        self.bill = create_bill_from_orders(partner_id=self.partner.id,
                                            order_ids=[str(order.id) for order in orders])
        self.pid = self.bill.public_id
        self.cashier = self._staff(CASHIER_TG, "split_cashier", User.Role.CASHIER)
        self._staff(WAITER_TG, "split_waiter", User.Role.WAITER)

    def _staff(self, telegram_id, username, role):
        user = User.objects.create_user(username=username, password="x", partner=self.partner,
                                        role=role)
        return EmployeeProfile.objects.create(
            partner=self.partner, user=user,
            telegram_account=TelegramAccount.objects.create(telegram_id=telegram_id),
        )

    def _press(self, handler, data, telegram_id=CASHIER_TG):
        callback = SimpleNamespace(
            data=data, from_user=SimpleNamespace(id=telegram_id),
            message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()), answer=AsyncMock(),
        )
        with patch.object(staff, "resolve_partner_for_bot_token", return_value=self.partner):
            async_to_sync(handler)(callback, SimpleNamespace(token="x"))
        return callback

    def _card(self):
        return self._press(staff.staff_bill_open_or_refresh_callback_handler,
                           f"staffbillrefresh:{self.pid}")

    @staticmethod
    def _shown(callback):
        call = callback.message.edit_text.call_args
        markup = call.kwargs["reply_markup"]
        buttons = [button for row in markup.inline_keyboard for button in row] if markup else []
        return call.args[0], {button.callback_data: button.text for button in buttons}

    def _split(self, parts):
        return self._press(staff_bill_split.bill_split_handler,
                           f"staffbillsplit:{self.pid}:{parts}")

    def test_the_card_offers_equal_parts_on_two_to_eight(self):
        _text, buttons = self._shown(self._card())
        self.assertIn(f"staffbillsplit:{self.pid}", buttons)
        _text, choices = self._shown(
            self._press(staff_bill_split.bill_split_handler, f"staffbillsplit:{self.pid}"))
        self.assertEqual([data for data in choices if data.count(":") == 2],
                         [f"staffbillsplit:{self.pid}:{parts}" for parts in range(2, 9)])
        self.assertIn(f"staffbillrefresh:{self.pid}", choices)
        self.assertNotIn(f"staffbillsplit:{self.pid}:0", choices)  # nothing to undo yet

    def test_a_split_bill_is_paid_part_by_part(self):
        text, buttons = self._shown(self._split(3))
        self.assertEqual(Bill.objects.get(id=self.bill.id).split_parts, 3)
        for number, amount in ((1, "53.33"), (2, "53.33"), (3, "53.34")):
            self.assertIn(f"{number} — {amount} грн", text)
        self.assertIn(f"staffbillpart:{self.pid}:3:c:5334", buttons)
        self.assertIn(f"staffbillpart:{self.pid}:3:t:5334", buttons)
        self.assertNotIn(f"staffbillpaycash:{self.pid}", buttons)
        self.assertNotIn(f"staffbillpayterminal:{self.pid}", buttons)
        self.assertIn(f"staffbillsplit:{self.pid}", buttons)  # split again: nothing paid yet
        self.assertIn("&amp;", text)  # menu names stay escaped

        paid = self._press(staff_bill_split.bill_part_pay_handler,
                           f"staffbillpart:{self.pid}:1:c:5333")
        payment = Payment.objects.get(bill=self.bill)
        self.assertEqual((payment.split_part, payment.amount, payment.method),
                         (1, Decimal("53.33"), Payment.Method.CASH))
        text, buttons = self._shown(paid)
        self.assertIn("✓", text)
        self.assertNotIn(f"staffbillpart:{self.pid}:1:c:5333", buttons)
        self.assertNotIn(f"staffbillsplit:{self.pid}", buttons)  # a part is paid
        self.assertIn(f"staffbillpart:{self.pid}:2:t:5333", buttons)

        self._press(staff_bill_split.bill_part_pay_handler, f"staffbillpart:{self.pid}:2:t:5333")
        self._press(staff_bill_split.bill_part_pay_handler, f"staffbillpart:{self.pid}:3:c:5334")
        self.assertEqual(Bill.objects.get(id=self.bill.id).status, Bill.Status.PAID)

    def test_a_stale_amount_is_refused(self):
        self._split(2)
        callback = self._press(staff_bill_split.bill_part_pay_handler,
                               f"staffbillpart:{self.pid}:1:c:7000")
        self.assertIn("изменилась", callback.answer.call_args.args[0])
        self.assertTrue(callback.answer.call_args.kwargs.get("show_alert"))
        self.assertFalse(Payment.objects.filter(bill=self.bill).exists())

    def test_the_split_can_be_undone_before_a_part_is_paid(self):
        self._split(2)
        _text, choices = self._shown(
            self._press(staff_bill_split.bill_split_handler, f"staffbillsplit:{self.pid}"))
        self.assertIn(f"staffbillsplit:{self.pid}:0", choices)
        _text, buttons = self._shown(self._split(0))
        self.assertEqual(Bill.objects.get(id=self.bill.id).split_parts, 0)
        self.assertIn(f"staffbillpaycash:{self.pid}", buttons)

    def test_only_who_takes_money_splits(self):
        for handler, data in ((staff_bill_split.bill_split_handler, f"staffbillsplit:{self.pid}:2"),
                              (staff_bill_split.bill_part_pay_handler,
                               f"staffbillpart:{self.pid}:1:c:8000")):
            callback = self._press(handler, data, telegram_id=WAITER_TG)
            self.assertTrue(callback.answer.call_args.kwargs.get("show_alert"))
        self.assertEqual(Bill.objects.get(id=self.bill.id).split_parts, 0)

    def test_the_tables_shared_bill_is_paid_by_its_guests_parts_not_split_again(self):
        # «Общее» of the settlement: its parts are guests' (PQ-009, step 3).
        split_bill_equally(partner_id=self.partner.id, employee=self.cashier,
                           bill_id=self.bill.id, parts=2)
        guest = self.bill.items.first().order.guest
        BillSplitShare.objects.create(partner=self.partner, bill=self.bill, number=1, guest=guest)
        BillSplitShare.objects.create(partner=self.partner, bill=self.bill, number=2, guest=guest)
        _text, buttons = self._shown(self._card())
        self.assertNotIn(f"staffbillsplit:{self.pid}", buttons)
        self.assertIn(f"staffbillpart:{self.pid}:1:c:8000", buttons)
