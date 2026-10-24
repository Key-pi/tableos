"""Staff refunds in the bot: choose lines, reason, how the money went back (PQ-037)."""

from decimal import Decimal
from unittest.mock import patch

from asgiref.sync import async_to_sync

from apps.billing.models import Payment, Refund
from apps.billing.remote_orders import record_payment_on_receipt
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Order
from apps.orders.services import add_items_to_cart_for_telegram_user
from apps.partners.models import PartnerBotSettings
from apps.users.models import TelegramAccount, User
from bot.handlers import staff, staff_refunds
from bot.tests_remote_checkout import GUEST, STAFF, PickupBotFixture, _buttons


def _data(markup):
    return [button.callback_data for button in _buttons(markup)]


class StaffRefundBotTests(PickupBotFixture):
    def setUp(self):
        super().setUp()
        self._set_phone()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            pickup_pay_transfer=False, pickup_pay_on_receipt=True
        )
        MenuItem.objects.create(
            partner=self.partner,
            category=MenuCategory.objects.get(partner=self.partner),
            public_id="PB000002",
            name="Компот",
            price="50.00",
        )
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=GUEST, raw_items="PB000002x1"
        )
        self.owner = User.objects.create_user(
            username="refund_bot_owner", password="x", partner=self.partner, role=User.Role.OWNER
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.owner,
            telegram_account=TelegramAccount.objects.create(telegram_id=STAFF),
        )
        self._press("rc:p:p:now:0:35000:35000:r")
        self.order = Order.objects.get()
        record_payment_on_receipt(order=self.order, actor=self.owner, method=Payment.Method.CASH)
        self.order.refresh_from_db()

    def _staff(self, data, telegram_id=STAFF):
        callback = self._callback(data, telegram_id=telegram_id)
        with patch.object(
            staff, "resolve_partner_for_bot_token", return_value=self.partner
        ), patch("apps.notifications.services.send_guest_refund_notice"):
            async_to_sync(staff_refunds.staff_refund_callback)(callback, self.bot)
        return callback

    def _screen(self, callback):
        args = callback.message.edit_text.call_args
        return args.args[0], _data(args.kwargs["reply_markup"])

    def test_paid_order_card_offers_a_refund(self):
        order = Order.objects.select_related("partner", "guest__telegram_account").get(
            id=self.order.id
        )
        self.assertIn(f"strf:{order.public_id}:0", _data(staff._order_actions_keyboard(order)))

    def test_partial_refund_step_by_step(self):
        pid = self.order.public_id
        callback = self._staff(f"strf:{pid}:0")
        text, data = self._screen(callback)
        self.assertIn("Отметьте, что вернуть", text)
        markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
        compot = next(b.callback_data for b in _buttons(markup) if "Компот" in b.text)
        m = compot.rsplit(":", 1)[1]
        text, data = self._screen(self._staff(compot))
        self.assertIn(f"strr:{pid}:{m}", data)
        text, data = self._screen(self._staff(f"strr:{pid}:{m}"))
        self.assertIn(f"strm:{pid}:{m}:os", data)
        text, data = self._screen(self._staff(f"strm:{pid}:{m}:os"))
        self.assertIn(f"strc:{pid}:{m}:os:c", data)
        text, data = self._screen(self._staff(f"strc:{pid}:{m}:os:c"))
        self.assertIn("Деньги гостю: <b>50.00 грн</b>", text)
        self.assertIn(f"strk:{pid}:{m}:os:c", data)
        callback = self._staff(f"strk:{pid}:{m}:os:c")
        refund = Refund.objects.get()
        self.assertEqual(refund.money_amount, Decimal("50.00"))
        self.assertEqual(refund.reason_code, Refund.ReasonCode.OUT_OF_STOCK)
        self.assertEqual(refund.method, Refund.Method.CASH)
        self.assertIn("Возврат оформлен", callback.message.edit_text.call_args.args[0])
        # The refunded line is shown as such and cannot be chosen again.
        text, data = self._screen(self._staff(f"strf:{pid}:0"))
        self.assertIn("Компот × 1 — 50.00 грн — уже возвращено", text)
        self.assertNotIn(compot, data)

    def test_staff_without_the_right_is_refused(self):
        cashier = User.objects.create_user(
            username="refund_bot_cashier",
            password="x",
            partner=self.partner,
            role=User.Role.CASHIER,
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=cashier,
            telegram_account=TelegramAccount.objects.create(telegram_id=7300999),
        )
        callback = self._staff(f"strf:{self.order.public_id}:0", telegram_id=7300999)
        callback.message.edit_text.assert_not_called()
        self.assertIn("Нет права", callback.answer.call_args.args[0])

    def test_tampered_callback_is_refused(self):
        callback = self._staff(f"strk:{self.order.public_id}:zz:os:c")
        self.assertIn("Неизвестное действие", callback.answer.call_args.args[0])
        self.assertFalse(Refund.objects.exists())

    def test_guest_sees_the_refund_in_my_orders(self):
        from bot.handlers import profile

        pid = self.order.public_id
        self._staff(f"strk:{pid}:3:q:t")  # both lines
        callback = self._callback(f"guestorder:{pid}")
        async_to_sync(profile.my_orders_callback)(callback, self.bot)
        self.assertIn("Возвращено: 350.00 грн", callback.message.edit_text.call_args.args[0])
