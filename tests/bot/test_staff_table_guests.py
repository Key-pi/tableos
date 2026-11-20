"""The table card in the staff bot, the new way (PQ-009 in the bot, step 2;
owner 2026-11-20: «Оплата за гостя», «Гости стола по-новому»): the people of
the company — with the bot and without («Гость 1») — with what each has to
pay, «Гостей за столом» with ➖/➕, one tap to take a guest's money by cash
or terminal, and «🧾 Рассчитать в Mini App» for moving lines."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.billing.models import Payment
from apps.billing.table_bills import create_personal_bills_for_table
from apps.billing.visit_settlement import SHARED, lay_out_visit, visit_settlement
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import OrderItem
from apps.orders.services import create_order_from_session, create_staff_table_order
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.models import Table, TableVisit
from apps.tables.services import activate_table_session
from apps.users.models import TelegramAccount, User
from bot.handlers import staff, staff_table_guests
from tests.modules import TABLE_VENUE_MODULES

CASHIER_TG, OLYA_TG = 7751001, 7751009


class BotTableGuestsTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(name="Guests Bot", slug="guests-bot",
                                              status=Partner.Status.ACTIVE)
        self.settings = PartnerBotSettings.objects.create(
            partner=self.partner, **TABLE_VENUE_MODULES, module_billing_enabled=True,
            module_floor_plan_enabled=True, module_table_orders_enabled=True,
        )
        self.table = Table.objects.create(partner=self.partner, number=5, qr_token="guestsbot05")
        category = MenuCategory.objects.create(partner=self.partner, name="Bar")
        self.tea = MenuItem.objects.create(partner=self.partner, category=category,
                                           name="Чай <чорний>", price="90.00")
        session = activate_table_session(partner_id=self.partner.id, telegram_id=OLYA_TG,
                                         payload=self.table.deep_link_payload)
        TelegramAccount.objects.filter(telegram_id=OLYA_TG).update(first_name="Оля")
        self.visit = TableVisit.objects.get(id=session.visit_id)
        self.hex = self.visit.id.hex
        create_order_from_session(
            partner_id=self.partner.id, table_session=session,
            items=[{"menu_item_id": self.tea.id, "item_name": self.tea.name,
                    "unit_price": self.tea.price, "quantity": 1}],
        )
        user = User.objects.create_user(username="guests_cashier", password="x",
                                        partner=self.partner, role=User.Role.CASHIER)
        self.cashier = EmployeeProfile.objects.create(
            partner=self.partner, user=user,
            telegram_account=TelegramAccount.objects.create(telegram_id=CASHIER_TG),
        )
        # One more person without the bot, ordered from the tablet.
        create_staff_table_order(partner_id=self.partner.id, actor=self.cashier,
                                 table_id=self.table.id, visit_id=self.visit.id, new_guest=True,
                                 items=[{"menu_item_id": self.tea.id, "quantity": 2}])

    def _press(self, handler, data, link=""):
        callback = SimpleNamespace(
            data=data, from_user=SimpleNamespace(id=CASHIER_TG),
            message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()), answer=AsyncMock(),
        )
        with (patch.object(staff, "resolve_partner_for_bot_token", return_value=self.partner),
              patch.object(staff, "staff_screen_link_for", return_value=link)):
            async_to_sync(handler)(callback, SimpleNamespace(token="x"))
        return callback

    def _card(self, link=""):
        return self._press(staff.staff_table_open_callback_handler,
                           f"stafftableopen:{self.table.id}", link)

    @staticmethod
    def _shown(callback):
        call = callback.message.edit_text.call_args
        rows = call.kwargs["reply_markup"].inline_keyboard
        buttons = [button for row in rows for button in row]
        return call.args[0], {button.callback_data: button.text for button in buttons
                              if button.callback_data}, buttons

    def _guests(self):
        return {guest.label: guest for guest in
                visit_settlement(partner_id=self.partner.id, visit_id=self.visit.id).guests}

    def test_the_card_shows_the_company_and_how_many_sit(self):
        text, buttons, _all = self._shown(self._card())
        self.assertIn("Гостей за столом: 2", text)
        self.assertIn("Оля", text)
        self.assertIn("Гость 1", text)
        self.assertIn("без счёта 90.00 грн", text)
        self.assertIn("без счёта 180.00 грн", text)
        self.assertNotIn("Активных гостей", text)
        self.assertIn(f"stafftvcount:{self.hex}:3", buttons)
        # No fewer than the people at the table: no ➖ at two.
        self.assertNotIn(f"stafftvcount:{self.hex}:1", buttons)
        self.assertTrue(all(len(data) <= 64 for data in buttons))

    def test_how_many_sit_goes_up_and_down(self):
        self._press(staff_table_guests.visit_count_handler, f"stafftvcount:{self.hex}:4")
        self.assertEqual(TableVisit.objects.get(id=self.visit.id).guests_count, 4)
        text, buttons, _all = self._shown(self._card())
        self.assertIn("Гостей за столом: 4", text)
        self.assertIn(f"stafftvcount:{self.hex}:3", buttons)
        self.assertIn(f"stafftvcount:{self.hex}:5", buttons)

    def test_a_guests_money_in_one_tap(self):
        create_personal_bills_for_table(partner_id=self.partner.id, table_id=self.table.id,
                                        visit_id=self.visit.id)
        guest = self._guests()["Гость 1"]
        code = guest_code(guest.guest_id)
        text, buttons, _all = self._shown(self._card())
        self.assertIn("к оплате 180.00 грн", text)
        pay = f"stafftvpay:{self.hex}:{code}:c:18000"
        self.assertIn(pay, buttons)
        self.assertIn("Гость 1", buttons[pay])
        callback = self._press(staff_table_guests.visit_pay_handler, pay)
        payment = Payment.objects.get()
        self.assertEqual((payment.amount, payment.method, payment.bill.primary_guest_id),
                         (Decimal("180.00"), Payment.Method.CASH, guest.guest_id))
        _text, buttons, _all = self._shown(callback)
        self.assertNotIn(pay, buttons)
        self.assertTrue(any(data.startswith(f"stafftvpay:{self.hex}:") for data in buttons))

    def test_a_guest_pays_their_part_of_the_shared_lines(self):
        guests = self._guests()
        lines = OrderItem.objects.filter(order__table_session__visit_id=self.visit.id)
        lay_out_visit(partner_id=self.partner.id, employee=self.cashier, visit_id=self.visit.id,
                      places={str(line.id): SHARED for line in lines},
                      sharers=[str(guest.guest_id) for guest in guests.values()])
        olya = next(guest for guest in guests.values() if guest.has_bot)
        pay = f"stafftvpay:{self.hex}:{guest_code(olya.guest_id)}:t:13500"
        _text, buttons, _all = self._shown(self._card())
        self.assertIn(pay, buttons)
        self._press(staff_table_guests.visit_pay_handler, pay)
        payment = Payment.objects.get()
        self.assertEqual((payment.amount, payment.method, payment.split_part),
                         (Decimal("135.00"), Payment.Method.TERMINAL, 1))

    def test_a_stale_amount_is_refused(self):
        create_personal_bills_for_table(partner_id=self.partner.id, table_id=self.table.id,
                                        visit_id=self.visit.id)
        code = guest_code(self._guests()["Гость 1"].guest_id)
        callback = self._press(staff_table_guests.visit_pay_handler,
                               f"stafftvpay:{self.hex}:{code}:c:9000")
        self.assertIn("изменилась", callback.answer.call_args.args[0])
        self.assertFalse(Payment.objects.exists())

    def test_without_billing_no_money_buttons(self):
        create_personal_bills_for_table(partner_id=self.partner.id, table_id=self.table.id,
                                        visit_id=self.visit.id)
        self.settings.module_billing_enabled = False  # the partner carries this object
        self.settings.save(update_fields=["module_billing_enabled"])
        _text, buttons, _all = self._shown(self._card())
        self.assertFalse(any(data.startswith("stafftvpay:") for data in buttons))
        self.assertIn(f"stafftvcount:{self.hex}:3", buttons)

    def test_moving_lines_opens_the_settlement_in_the_mini_app(self):
        _text, _buttons, every = self._shown(self._card(link="https://shop.example.com/m/x/staff/"))
        urls = [button.web_app.url for button in every if button.web_app]
        self.assertEqual(urls, [f"https://shop.example.com/m/x/staff/?settle={self.hex}"])
        _text, _buttons, every = self._shown(self._card(link=""))
        self.assertFalse([button for button in every if button.web_app])


def guest_code(guest_id):
    from apps.users.models import GuestProfile

    return GuestProfile.objects.get(id=guest_id).customer_code
