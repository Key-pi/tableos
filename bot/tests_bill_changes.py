"""Staff bot flows for bill changes (B3.2 part A)."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.billing.models import Bill, BillAdjustment, BillItem
from apps.billing.services import create_bill_from_orders
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Order
from apps.orders.services import create_order_from_session
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import TelegramAccount, User
from bot.handlers import staff


class _FakeState:
    def __init__(self):
        self.state = None
        self.data = {}

    async def set_state(self, value):
        self.state = value

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.state = None
        self.data = {}


class StaffBillChangeHandlerTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Bot Correction Venue",
            slug="bot-correction-venue",
            status=Partner.Status.ACTIVE,
        )
        PartnerBotSettings.objects.create(partner=self.partner, module_billing_enabled=True)
        table = Table.objects.create(partner=self.partner, number=3, qr_token="botcorr03")
        category = MenuCategory.objects.create(partner=self.partner, name="Bar")
        menu_item = MenuItem.objects.create(
            partner=self.partner, category=category, name="Cola & Ice", price="80.00"
        )
        session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=7730001,
            payload=table.deep_link_payload,
        )
        self.orders = [
            create_order_from_session(
                partner_id=self.partner.id,
                table_session=session,
                items=[
                    {
                        "menu_item_id": menu_item.id,
                        "item_name": menu_item.name,
                        "unit_price": menu_item.price,
                        "quantity": 1,
                    }
                ],
            )
            for _ in range(2)
        ]
        self.bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(order.id) for order in self.orders],
        )
        self.granted = self._cashier(7739001, "granted", can_adjust_bills=True)
        self.plain = self._cashier(7739002, "plain", can_adjust_bills=False)

    def _cashier(self, telegram_id, username, *, can_adjust_bills):
        user = User.objects.create_user(
            username=username,
            password="pass12345",
            partner=self.partner,
            role=User.Role.CASHIER,
        )
        account = TelegramAccount.objects.create(telegram_id=telegram_id, username=username)
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=user,
            telegram_account=account,
            can_adjust_bills=can_adjust_bills,
        )
        return telegram_id

    def _callback(self, telegram_id, data):
        message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
        return SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=telegram_id),
            message=message,
            answer=AsyncMock(),
        )

    def _run(self, handler, *args):
        with patch.object(staff, "resolve_partner_for_bot_token", return_value=self.partner):
            async_to_sync(handler)(*args)

    def _button_callbacks(self, callback):
        markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
        return [button.callback_data for row in markup.inline_keyboard for button in row]

    def test_bill_card_offers_change_buttons_only_to_granted_staff(self):
        bot = SimpleNamespace(token="token")
        granted = self._callback(self.granted, f"staffbillrefresh:{self.bill.public_id}")
        plain = self._callback(self.plain, f"staffbillrefresh:{self.bill.public_id}")

        self._run(staff.staff_bill_open_or_refresh_callback_handler, granted, bot)
        self._run(staff.staff_bill_open_or_refresh_callback_handler, plain, bot)

        self.assertIn(f"staffbilledit:{self.bill.public_id}", self._button_callbacks(granted))
        self.assertIn(f"staffbillvoid:{self.bill.public_id}", self._button_callbacks(granted))
        self.assertNotIn(f"staffbilledit:{self.bill.public_id}", self._button_callbacks(plain))

    def test_remove_line_with_reason_and_add_comment(self):
        bot = SimpleNamespace(token="token")
        line = BillItem.objects.get(bill=self.bill, order=self.orders[1])

        edit = self._callback(self.granted, f"staffbilledit:{self.bill.public_id}")
        self._run(staff.staff_bill_edit_callback_handler, edit, bot)
        self.assertIn(f"staffbillrm:{line.order_item_id}", self._button_callbacks(edit))
        markup = edit.message.edit_text.call_args.kwargs["reply_markup"]
        labels = [button.text for row in markup.inline_keyboard for button in row]
        # Button labels are plain text: no HTML escaping there.
        self.assertIn("Убрать: Cola & Ice x1 (80.00 грн)", labels)

        remove = self._callback(self.granted, f"staffbillrr:{line.order_item_id}:c")
        self._run(staff.staff_bill_remove_line_handler, remove, bot)

        self.bill.refresh_from_db()
        line.refresh_from_db()
        self.assertFalse(line.is_active)
        self.assertEqual(self.bill.total_amount, Decimal("80.00"))
        adjustment = BillAdjustment.objects.get(bill=self.bill)
        self.assertEqual(adjustment.reason_code, BillAdjustment.ReasonCode.COMPLIMENT)

        state = _FakeState()
        prompt = self._callback(self.granted, f"staffadjc:{adjustment.id}")
        with patch.object(staff, "resolve_partner_for_bot_token", return_value=self.partner):
            async_to_sync(staff.staff_adjustment_comment_prompt_handler)(prompt, bot, state)
            message = SimpleNamespace(
                text="birthday guest",
                from_user=SimpleNamespace(id=self.granted),
                answer=AsyncMock(),
            )
            async_to_sync(staff.staff_adjustment_comment_message_handler)(message, bot, state)

        adjustment.refresh_from_db()
        self.assertEqual(adjustment.reason_text, "birthday guest")

    def test_plain_cashier_cannot_remove_lines(self):
        bot = SimpleNamespace(token="token")
        line = BillItem.objects.get(bill=self.bill, order=self.orders[1])
        remove = self._callback(self.plain, f"staffbillrr:{line.order_item_id}:e")

        self._run(staff.staff_bill_remove_line_handler, remove, bot)

        line.refresh_from_db()
        self.assertTrue(line.is_active)
        self.assertIn("Нет права", remove.answer.call_args.args[0])

    def test_void_from_bot(self):
        bot = SimpleNamespace(token="token")
        void = self._callback(self.granted, f"staffbillvr:{self.bill.public_id}:o")

        self._run(staff.staff_bill_void_handler, void, bot)

        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, Bill.Status.CANCELED)

    def test_order_card_cancel_of_billed_order_points_to_the_bill(self):
        bot = SimpleNamespace(token="token")
        order = self.orders[0]
        cancel = self._callback(self.granted, f"stafforder:{order.public_id}:canceled")

        self._run(staff.staff_order_status_callback_handler, cancel, bot)

        order.refresh_from_db()
        self.assertNotEqual(order.status, Order.Status.CANCELED)
        self.assertIn("через карточку счёта", cancel.answer.call_args.args[0])

    def test_cancel_order_from_bill_card(self):
        bot = SimpleNamespace(token="token")
        order = self.orders[1]
        cancel = self._callback(self.granted, f"staffbillor:{order.public_id}:g")

        self._run(staff.staff_bill_cancel_order_handler, cancel, bot)

        order.refresh_from_db()
        self.bill.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELED)
        self.assertEqual(self.bill.total_amount, Decimal("80.00"))
