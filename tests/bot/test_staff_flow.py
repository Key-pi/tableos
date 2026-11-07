"""The staff side of the bot (2026-11-04): one screen per section with paging,
the work-mode button, cards without Telegram IDs and internal noise."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.utils import timezone

from apps.employees.models import EmployeeProfile
from apps.notifications.models import StaffNotification
from apps.orders.models import Order
from apps.orders.services import (
    add_items_to_cart_for_telegram_user,
    checkout_active_cart_for_telegram_user,
    switch_cart_channel_for_telegram_user,
)
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import TelegramAccount, User
from bot.handlers import staff
from bot.keyboards.main import build_main_keyboard
from bot.services.content import BotContent
from bot.services.navigation import GuestAction, resolve_guest_navigation_state
from tests.bot.test_guest_flow import GUEST, GuestFlowFixture

OWNER_TG = 7730001


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _data(markup):
    return [button.callback_data for button in _buttons(markup)]


class StaffFlowFixture(GuestFlowFixture):
    def setUp(self):
        super().setUp()
        self.owner_user = User.objects.create_user(
            username="flow_owner",
            password="x",
            partner=self.partner,
            role=User.Role.OWNER,
            first_name="Світлана",
        )
        self.owner = EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.owner_user,
            telegram_account=TelegramAccount.objects.create(telegram_id=OWNER_TG),
            bot_notifications_enabled=True,
            notify_on_order_created=True,
            notify_on_order_status_changed=True,
            notify_on_guest_calls=True,
            notify_on_billing_requests=True,
        )
        account = self.guest.telegram_account
        account.first_name, account.username = "Оля", "olya"
        account.save(update_fields=["first_name", "username"])

    def _staff_callback(self, data):
        return SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=OWNER_TG, language_code=""),
            answer=AsyncMock(),
            message=SimpleNamespace(
                answer=AsyncMock(), edit_text=AsyncMock(), html_text="", text=""
            ),
        )

    def _press(self, handler, data):
        callback = self._staff_callback(data)
        async_to_sync(handler)(callback, self.bot)
        return callback

    def _screen(self, callback):
        call = callback.message.edit_text.call_args
        return call.args[0], call.kwargs["reply_markup"]

    def _pickup_order(self, *, quantity=1):
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, channel=Order.Channel.PICKUP
        )
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, raw_items=f"FLOW0001x{quantity}"
        )

    def _table_orders(self, count):
        table = Table.objects.create(partner=self.partner, number=3, qr_token="staff-flow-3")
        activate_table_session(
            partner_id=self.partner.id,
            telegram_id=GUEST,
            payload=table.deep_link_payload,
            username="olya",
            first_name="Оля",
        )
        orders = []
        for _number in range(count):
            add_items_to_cart_for_telegram_user(
                partner_id=self.partner.id, telegram_id=GUEST, raw_items="FLOW0001x1"
            )
            orders.append(
                checkout_active_cart_for_telegram_user(
                    partner_id=self.partner.id, telegram_id=GUEST
                )
            )
        return table, orders


class WorkModeButtonTests(StaffFlowFixture):
    def _state(self, telegram_id):
        return resolve_guest_navigation_state(
            partner_id=self.partner.id,
            telegram_id=telegram_id,
            content=BotContent.for_partner(self.partner),
        )

    def test_an_employee_gets_the_work_mode_button_and_a_guest_does_not(self):
        content = BotContent.for_partner(self.partner)
        owner_keyboard = build_main_keyboard(content, navigation_state=self._state(OWNER_TG))
        labels = [button.text for row in owner_keyboard.keyboard for button in row]
        self.assertIn("🧑‍🍳 Рабочий режим", labels)
        self.assertFalse(self._state(GUEST).has_action(GuestAction.STAFF))

    def test_an_inactive_employee_gets_no_button(self):
        EmployeeProfile.objects.filter(id=self.owner.id).update(is_active=False)
        self.assertFalse(self._state(OWNER_TG).has_action(GuestAction.STAFF))

    def test_the_button_opens_the_work_mode(self):
        message = SimpleNamespace(
            text="🧑‍🍳 Рабочий режим",
            from_user=SimpleNamespace(id=OWNER_TG, language_code=""),
            answer=AsyncMock(),
        )
        async_to_sync(staff.staff_help_handler)(message, self.bot)
        text = message.answer.call_args.args[0]
        self.assertIn("<b>Рабочий режим</b> · Flow Cafe", text)
        self.assertNotIn("резервный", text)
        self.assertIn("stafforders:refresh", _data(message.answer.call_args.kwargs["reply_markup"]))


class OrdersListTests(StaffFlowFixture):
    def test_a_prepaid_order_offers_the_transfer_check_not_taking_it_into_work(self):
        from apps.billing.remote_orders import place_remote_order

        self._pickup_order()
        self.guest.phone = "+380671234567"
        self.guest.save(update_fields=["phone"])
        order = place_remote_order(
            partner_id=self.partner.id,
            telegram_id=GUEST,
            slot="now",
            use_bonuses=False,
            expected_total="220.00",
            expected_to_pay="220.00",
        )
        text, markup = self._screen(self._press(staff.staff_orders_refresh_callback_handler,
                                                "stafforders:refresh"))
        row = next(r for r in markup.inline_keyboard if order.public_id in r[0].text)
        self.assertEqual(row[1].text, "💳 Проверить оплату")
        self.assertEqual(row[1].callback_data, f"stafforderopen:{order.public_id}")

    def test_many_orders_are_paged(self):
        _table, orders = self._table_orders(10)
        _text, markup = self._screen(
            self._press(staff.staff_orders_refresh_callback_handler, "stafforders:refresh")
        )
        opened = [d for d in _data(markup) if d.startswith("stafforderopen:")]
        self.assertEqual(len(opened), 8)
        self.assertIn("stafforders:p:1", _data(markup))

        text, markup = self._screen(
            self._press(staff.staff_orders_refresh_callback_handler, "stafforders:p:1")
        )
        self.assertEqual(len([d for d in _data(markup) if d.startswith("stafforderopen:")]), 2)
        self.assertIn("Страница 2 из 2", text)

    def test_the_order_card_names_the_guest_and_leads_back(self):
        _table, (order,) = self._table_orders(1)
        callback = self._press(
            staff.staff_order_open_callback_handler, f"stafforderopen:{order.public_id}"
        )
        text, markup = self._screen(callback)
        self.assertIn("Гость: Оля @olya · ", text)
        self.assertNotIn(str(GUEST), text)
        self.assertNotIn("Без комментария", text)
        self.assertIn("stafforders:refresh", _data(markup))

    def test_the_status_toast_names_the_new_status(self):
        _table, (order,) = self._table_orders(1)
        callback = self._press(
            staff.staff_order_status_callback_handler, f"stafforder:{order.public_id}:accepted"
        )
        self.assertEqual(callback.answer.call_args.args[0], "Статус: Принят")


class NotificationsListTests(StaffFlowFixture):
    def _notify(self, count):
        for number in range(count):
            StaffNotification.objects.create(
                partner=self.partner,
                employee=self.owner,
                category=StaffNotification.Category.TABLE_VISIT,
                title=f"Стол #{number}: новый визит",
                message="…",
            )

    def test_the_list_is_one_message_with_pages(self):
        self._notify(11)
        callback = self._press(staff.staff_notifications_refresh_callback_handler,
                               "staffnotif:refresh")
        callback.message.answer.assert_not_awaited()
        text, markup = self._screen(callback)
        self.assertIn("непрочитанных: 11", text)
        self.assertEqual(len([d for d in _data(markup) if d.startswith("staffnotifopen:")]), 8)
        self.assertIn("staffnotif:p:1", _data(markup))
        for data in _data(markup):
            self.assertLessEqual(len(data.encode()), 64)

    def test_open_read_and_back_to_the_same_page(self):
        self._notify(9)
        notification = StaffNotification.objects.order_by("created_at").first()
        hex_id = notification.id.hex
        _text, markup = self._screen(
            self._press(staff.staff_notification_open_callback_handler,
                        f"staffnotifopen:{hex_id}:1")
        )
        self.assertIn("staffnotif:p:1", _data(markup))
        self.assertIn(f"staffnotifread:{hex_id}:1", _data(markup))

        text, _markup = self._screen(
            self._press(staff.staff_notification_read_one_callback_handler,
                        f"staffnotifread:{hex_id}:1")
        )
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertIn("непрочитанных: 8", text)


class BillingAndTablesTests(StaffFlowFixture):
    def test_bills_are_one_list_and_a_card_opens_from_it(self):
        from apps.billing.table_bills import create_shared_bill_for_table

        table, _orders = self._table_orders(2)
        bill = create_shared_bill_for_table(partner_id=self.partner.id, table_id=table.id)
        callback = self._press(staff.staff_billing_refresh_callback_handler, "staffbill:list")
        callback.message.answer.assert_not_awaited()
        _text, markup = self._screen(callback)
        self.assertIn(f"staffbillopen:{bill.public_id}", _data(markup))

        text, markup = self._screen(
            self._press(staff.staff_bill_open_or_refresh_callback_handler,
                        f"staffbillopen:{bill.public_id}")
        )
        self.assertIn("стол 3", text)
        self.assertNotIn("Table #", text)
        self.assertNotIn("automatically", text)
        self.assertIn("staffbill:list", _data(markup))

    def test_a_paid_bill_offers_no_bonuses(self):
        from apps.billing.models import Bill, Payment
        from apps.billing.services import record_payment
        from apps.billing.table_bills import create_shared_bill_for_table

        table, _orders = self._table_orders(1)
        bill = create_shared_bill_for_table(partner_id=self.partner.id, table_id=table.id)
        record_payment(bill=bill, amount=bill.total_amount, method=Payment.Method.CASH)
        bill = Bill.objects.get(id=bill.id)
        _can_pay, text = staff._build_bill_card_view(bill)
        self.assertNotIn("можно списать", text)
        self.assertNotIn("К оплате", text)

    def test_table_card_shows_the_guest_time_in_the_venue_timezone(self):
        table, _orders = self._table_orders(1)
        session = table.sessions.get()
        started = timezone.now().replace(hour=1, minute=17, second=0, microsecond=0)
        type(session).objects.filter(id=session.id).update(started_at=started)
        text, _keyboard = staff._build_table_ops_view(partner_id=self.partner.id, table_id=table.id)
        local = timezone.localtime(started, timezone.get_default_timezone())
        self.assertIn(f"с {local:%H:%M}", text)
        self.assertIn("Оля @olya", text)


class QuickSaleDoneTests(StaffFlowFixture):
    def test_the_sale_card_leads_on(self):
        from bot.keyboards.staff import build_staff_sale_keyboard

        markup = build_staff_sale_keyboard(
            categories=[("1", "Еда"), ("2", "Кофе"), ("3", "Чай")],
            selected_category_id="1",
            category_items=[],
            draft_items=[],
            can_confirm=False,
            has_comment=False,
        )
        # Categories two to a row, not seven rows of one.
        self.assertEqual(len(markup.inline_keyboard[0]), 2)


class DeliveryCardTests(StaffFlowFixture):
    """A delivery paid on receipt (PQ-044): payment buttons from «В пути» on."""

    def setUp(self):
        super().setUp()
        from apps.billing.remote_orders import place_remote_order, save_guest_delivery_address
        from apps.partners.models import PartnerBotSettings

        PartnerBotSettings.objects.filter(partner=self.partner).update(
            delivery_pay_transfer=False, delivery_pay_on_receipt=True
        )
        self.guest.phone = "+380671234567"
        self.guest.save(update_fields=["phone"])
        save_guest_delivery_address(
            partner_id=self.partner.id, telegram_id=GUEST, address="вул. Хрещатик 1"
        )
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, channel=Order.Channel.DELIVERY
        )
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, raw_items="FLOW0001x1"
        )
        self.order = place_remote_order(
            partner_id=self.partner.id,
            telegram_id=GUEST,
            slot="now",
            use_bonuses=False,
            expected_total="220.00",
            expected_to_pay="220.00",
            channel=Order.Channel.DELIVERY,
            payment=Order.PaymentTiming.ON_RECEIPT,
        )

    def _card_data(self):
        order = Order.objects.select_related("partner").get(id=self.order.id)
        return _data(staff._order_actions_keyboard(order))

    def test_a_new_delivery_is_taken_into_work_or_cancelled_only(self):
        data = self._card_data()
        self.assertIn(f"stafforder:{self.order.public_id}:accepted", data)
        self.assertIn(f"staffrcno:{self.order.public_id}", data)
        self.assertNotIn(f"staffrccash:{self.order.public_id}", data)

    def test_on_the_way_the_courier_records_the_money(self):
        from apps.orders.services import transition_order_status

        for status in (Order.Status.ACCEPTED, Order.Status.READY, Order.Status.DELIVERING):
            self.order = transition_order_status(
                order=self.order, to_status=status, actor_user=self.owner_user
            )
        data = self._card_data()
        self.assertIn(f"staffrccash:{self.order.public_id}", data)
        self.assertIn(f"staffrccard:{self.order.public_id}", data)
        self.assertIn(f"staffrcno:{self.order.public_id}", data)


class StaffCallTakeTests(StaffFlowFixture):
    def test_take_from_the_pushed_message_and_from_the_list(self):
        from unittest.mock import patch

        from apps.notifications.models import GuestCall
        from apps.notifications.services import request_staff_assistance
        from apps.partners.models import BotInstance

        bot_instance = BotInstance.objects.get(partner=self.partner)
        bot_instance.set_token("323456789:ABCDEFGHIJKLMNOPQRST_uv-wxyz")
        bot_instance.is_active = True
        bot_instance.save()
        self._table_orders(1)
        with patch("apps.notifications.services._send_telegram_messages",
                   new=AsyncMock(return_value=[(0, True, "", False, False, 1)])):
            request_staff_assistance(
                partner_id=self.partner.id,
                telegram_id=GUEST,
                call_target="waiter",
                call_target_label="Официант",
            )
        notification = StaffNotification.objects.get(
            employee=self.owner, category=StaffNotification.Category.GUEST_CALL
        )
        _text, markup = self._screen(
            self._press(staff.staff_notification_open_callback_handler,
                        f"staffnotifopen:{notification.id.hex}:0")
        )
        self.assertIn(f"staffcalltake:{notification.id.hex}:0", _data(markup))

        with patch("apps.notifications.services.announce_guest_call_taken_now"):
            callback = self._press(staff.staff_call_take_callback_handler,
                                   f"staffcalltake:{notification.id.hex}")
        self.assertIn("вы идёте", callback.answer.call_args.args[0])
        self.assertIn("✓ Идёте вы", callback.message.edit_text.call_args.args[0])
        self.assertEqual(GuestCall.objects.get().taken_by_id, self.owner.id)
