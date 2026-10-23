"""Staff bot flows for table visits (B3.2 part B, ADR-0007)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.billing.models import Bill
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.services import create_order_from_session
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.models import Table, TableVisit
from apps.tables.services import VISIT_CHOICE_NEW, activate_table_session
from apps.users.models import TelegramAccount, User
from bot.handlers import staff


class StaffTableVisitHandlerTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Staff Visit Venue", slug="staff-visit-venue", status=Partner.Status.ACTIVE
        )
        PartnerBotSettings.objects.create(partner=self.partner, module_billing_enabled=True)
        self.table = Table.objects.create(partner=self.partner, number=9, qr_token="staffv09")
        category = MenuCategory.objects.create(partner=self.partner, name="Bar")
        self.menu_item = MenuItem.objects.create(
            partner=self.partner, category=category, name="Tea", price="90.00"
        )
        self.old_party = self._scan(781001)
        self.old_order = self._order(self.old_party)
        self.new_party = self._scan(781002, choice=VISIT_CHOICE_NEW)
        self._order(self.new_party)

        user = User.objects.create_user(
            username="visit_cashier",
            password="pass12345",
            partner=self.partner,
            role=User.Role.CASHIER,
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=user,
            telegram_account=TelegramAccount.objects.create(telegram_id=789001),
        )

    def _scan(self, telegram_id, choice=None):
        kwargs = {"visit_choice": choice} if choice else {}
        return activate_table_session(
            partner_id=self.partner.id,
            telegram_id=telegram_id,
            payload=self.table.deep_link_payload,
            **kwargs,
        )

    def _order(self, session):
        return create_order_from_session(
            partner_id=self.partner.id,
            table_session=session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": "Tea",
                    "unit_price": self.menu_item.price,
                    "quantity": 1,
                }
            ],
        )

    def _callback(self, data):
        return SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=789001),
            message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
            answer=AsyncMock(),
        )

    def _run(self, handler, callback):
        with patch.object(staff, "resolve_partner_for_bot_token", return_value=self.partner):
            async_to_sync(handler)(callback, SimpleNamespace(token="t"))

    def test_table_card_flags_several_visits_and_offers_visit_buttons(self):
        callback = self._callback(f"stafftableopen:{self.table.id}")

        self._run(staff.staff_table_open_callback_handler, callback)

        text = callback.message.answer.call_args.args[0]
        self.assertIn("⚠ За столом несколько визитов", text)
        markup = callback.message.answer.call_args.kwargs["reply_markup"]
        data = [button.callback_data for row in markup.inline_keyboard for button in row]
        old_hex = str(self.old_party.visit_id).replace("-", "")
        self.assertIn(f"stafftvshared:{old_hex}", data)
        self.assertTrue(all(len(item) <= 64 for item in data))

    def test_overview_marks_the_table(self):
        sessions, unbilled, visit_counts = staff._load_tables_feed_data(self.partner.id)

        shortcuts = staff._build_active_table_shortcuts(sessions, unbilled, visit_counts)

        self.assertTrue(shortcuts[0][1].startswith("⚠ Стол 9"))

    def test_staff_bills_and_cannot_close_the_old_visit_before_payment(self):
        old_hex = str(self.old_party.visit_id).replace("-", "")

        self._run(
            staff.staff_visit_bill_callback_handler,
            self._callback(f"stafftvshared:{old_hex}"),
        )
        bill = Bill.objects.get(visit_id=self.old_party.visit_id)
        self.assertEqual(
            list(bill.bill_orders.values_list("order_id", flat=True)), [self.old_order.id]
        )

        close = self._callback(f"stafftvclose:{old_hex}")
        self._run(staff.staff_visit_close_callback_handler, close)
        close.answer.assert_awaited()
        self.assertTrue(close.answer.call_args.kwargs.get("show_alert"))
        self.old_party.visit.refresh_from_db()
        self.assertEqual(self.old_party.visit.status, TableVisit.Status.ACTIVE)

    def test_visit_of_another_venue_is_not_found(self):
        other = Partner.objects.create(name="Other", slug="other-v", status="active")
        other_table = Table.objects.create(partner=other, number=1, qr_token="otherv1")
        foreign = activate_table_session(
            partner_id=other.id, telegram_id=781999, payload=other_table.deep_link_payload
        )
        callback = self._callback(f"stafftvclose:{str(foreign.visit_id).replace('-', '')}")

        self._run(staff.staff_visit_close_callback_handler, callback)

        callback.answer.assert_awaited_with("Визит не найден.", show_alert=True)
        foreign.visit.refresh_from_db()
        self.assertEqual(foreign.visit.status, TableVisit.Status.ACTIVE)
