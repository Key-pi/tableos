"""Bot flows for the payment options of remote modules (PQ-033)."""

from unittest.mock import patch

from asgiref.sync import async_to_sync

from apps.billing.models import Bill, Payment
from apps.employees.models import EmployeeProfile
from apps.orders.models import Order
from apps.partners.models import PartnerBotSettings
from apps.users.models import TelegramAccount, User
from bot.handlers import staff
from bot.tests_remote_checkout import STAFF, PickupBotFixture, _buttons


def _data(markup):
    return [button.callback_data for button in _buttons(markup)]


class GuestPaymentChoiceTests(PickupBotFixture):
    def setUp(self):
        super().setUp()
        self._set_phone()

    def _modes(self, *, transfer: bool, on_receipt: bool):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            pickup_pay_transfer=transfer, pickup_pay_on_receipt=on_receipt
        )

    def test_with_both_options_the_guest_chooses(self):
        self._modes(transfer=True, on_receipt=True)
        callback = self._press("rc:p:t:now")
        text = callback.message.edit_text.call_args.args[0]
        self.assertIn("Как оплатите?", text)
        data = _data(callback.message.edit_text.call_args.kwargs["reply_markup"])
        self.assertIn("rc:p:m:now:0:t", data)
        self.assertIn("rc:p:m:now:0:r", data)

    def test_paying_on_receipt_shows_no_requisites_and_places_the_order(self):
        self._modes(transfer=True, on_receipt=True)
        summary = self._press("rc:p:m:now:0:r")
        text = summary.message.edit_text.call_args.args[0]
        self.assertIn("К оплате при получении: 300.00 грн", text)
        self.assertNotIn("Реквизиты", text)
        place = next(
            data
            for data in _data(summary.message.edit_text.call_args.kwargs["reply_markup"])
            if data.startswith("rc:p:p:")
        )
        self.assertTrue(place.endswith(":r"))
        self.assertLessEqual(len(place.encode()), 64)

        placed = self._press(place)
        order = Order.objects.get()
        self.assertEqual(order.payment_timing, Order.PaymentTiming.ON_RECEIPT)
        self.assertIn("при получении", placed.message.edit_text.call_args.args[0])

    def test_with_one_option_there_is_no_question(self):
        self._modes(transfer=False, on_receipt=True)
        callback = self._press("rc:p:t:now")
        text = callback.message.edit_text.call_args.args[0]
        self.assertIn("К оплате при получении", text)

    def test_an_earlier_summary_without_the_option_means_transfer(self):
        self._press("rc:p:p:now:0:30000:30000")
        self.assertEqual(Order.objects.get().payment_timing, Order.PaymentTiming.PREPAID)


class StaffPaymentOnReceiptTests(PickupBotFixture):
    def setUp(self):
        super().setUp()
        self._set_phone()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            pickup_pay_transfer=False, pickup_pay_on_receipt=True
        )
        cashier = User.objects.create_user(
            username="receipt_bot_cashier",
            password="x",
            partner=self.partner,
            role=User.Role.CASHIER,
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=cashier,
            telegram_account=TelegramAccount.objects.create(telegram_id=STAFF),
        )
        self._press("rc:p:p:now:0:30000:30000:r")
        self.order = Order.objects.get()

    def _card(self):
        order = Order.objects.select_related("partner", "guest__telegram_account").get(
            id=self.order.id
        )
        return staff._render_order_card(order), _data(staff._order_actions_keyboard(order))

    def _staff(self, data):
        callback = self._callback(data, telegram_id=STAFF)
        with patch.object(staff, "resolve_partner_for_bot_token", return_value=self.partner):
            async_to_sync(staff.staff_receipt_payment_callback_handler)(callback, self.bot)
        return callback

    def test_card_offers_accepting_payment_and_refusal(self):
        text, data = self._card()
        public_id = self.order.public_id
        self.assertIn("при получении, 300.00 грн", text)
        self.assertIn(f"stafforder:{public_id}:accepted", data)
        self.assertIn(f"staffrccash:{public_id}", data)
        self.assertIn(f"staffrccard:{public_id}", data)
        self.assertIn(f"staffrcno:{public_id}", data)
        self.assertNotIn(f"stafforder:{public_id}:canceled", data)

    def test_card_payment_is_recorded(self):
        callback = self._staff(f"staffrccard:{self.order.public_id}")
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.paid_at)
        self.assertEqual(
            Bill.objects.get(status=Bill.Status.PAID).payments.get().method,
            Payment.Method.TERMINAL,
        )
        self.assertIn("Оплата", callback.answer.call_args.args[0])
        _, data = self._card()
        self.assertNotIn(f"staffrccash:{self.order.public_id}", data)

    def test_refusal_asks_first_then_cancels(self):
        ask = self._staff(f"staffrcno:{self.order.public_id}")
        self.assertIn("не забрал", ask.message.edit_text.call_args.args[0])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.NEW)

        self._staff(f"staffrcnoy:{self.order.public_id}")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELED)
