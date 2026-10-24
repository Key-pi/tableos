"""«Мои заказы» in the guest profile: status, payment and ТТН of own orders."""

from asgiref.sync import async_to_sync

from apps.billing.remote_orders import place_remote_order
from apps.billing.test_remote_orders import NOON
from apps.orders.models import Order
from apps.users.models import GuestProfile
from apps.users.services import get_or_create_guest_profile
from bot.handlers import profile
from bot.keyboards.profile import build_profile_summary_keyboard
from bot.tests_shipping_checkout import GUEST, ShippingBotFixture, _data

OTHER = 7700002


class MyOrdersTests(ShippingBotFixture):
    def setUp(self):
        super().setUp()
        GuestProfile.objects.filter(id=self.guest.id).update(
            shipping_carrier=Order.Carrier.NOVA_POSHTA,
            shipping_destination="Іваненко Іван, Київ, відділення 25",
        )
        self.order = place_remote_order(
            partner_id=self.partner.id,
            telegram_id=GUEST,
            slot="now",
            use_bonuses=False,
            expected_total="600.00",
            expected_to_pay="600.00",
            channel=Order.Channel.SHIPPING,
            payment=Order.PaymentTiming.ON_RECEIPT,
            shipping_cost=Order.ShippingCost.CARRIER,
            now=NOON,
        )
        Order.objects.filter(id=self.order.id).update(
            status=Order.Status.DELIVERING, tracking_number="20450000000001"
        )

    def _open(self, data, telegram_id=GUEST):
        callback = self._callback(data, telegram_id=telegram_id)
        async_to_sync(profile.my_orders_callback)(callback, self.bot)
        return callback

    def test_profile_has_a_my_orders_button(self):
        markup = build_profile_summary_keyboard(bonuses_label="Бонусы")
        self.assertIn("guestorders:0", _data(markup))

    def test_list_shows_own_orders_with_status_and_payment(self):
        callback = self._open("guestorders:0")
        text = callback.message.edit_text.await_args.args[0]
        self.assertIn(f"#{self.order.public_id}", text)
        self.assertIn("Отправка почтой", text)
        self.assertIn("В пути", text)
        self.assertIn("600", text)
        self.assertIn("при получении", text)
        markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
        self.assertIn(f"guestorder:{self.order.public_id}", _data(markup))

    def test_details_show_the_tracking_link_and_received_button(self):
        callback = self._open(f"guestorder:{self.order.public_id}")
        text = callback.message.edit_text.await_args.args[0]
        self.assertIn("ТТН <code>20450000000001</code>", text)
        self.assertIn("novaposhta.ua/tracking/?cargo_number=20450000000001", text)
        self.assertIn("Зерно", text)
        markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
        self.assertIn(f"guestorderconfirm:{self.order.public_id}", _data(markup))
        self.assertIn("guestorders:0", _data(markup))

    def test_another_guest_sees_neither_the_order_nor_its_details(self):
        get_or_create_guest_profile(partner_id=self.partner.id, telegram_id=OTHER)
        listing = self._open("guestorders:0", telegram_id=OTHER)
        self.assertNotIn(self.order.public_id, listing.message.edit_text.await_args.args[0])
        details = self._open(f"guestorder:{self.order.public_id}", telegram_id=OTHER)
        details.message.edit_text.assert_not_awaited()
        self.assertIn("не найден", details.answer.await_args.args[0])

    def test_guest_text_is_escaped(self):
        Order.objects.filter(id=self.order.id).update(shipping_destination="<b>Київ</b>")
        callback = self._open(f"guestorder:{self.order.public_id}")
        self.assertIn("&lt;b&gt;Київ&lt;/b&gt;", callback.message.edit_text.await_args.args[0])

    def test_long_history_is_paged(self):
        for _ in range(6):
            Order.objects.create(partner=self.partner, guest=self.guest)
        first = self._open("guestorders:0")
        markup = first.message.edit_text.await_args.kwargs["reply_markup"]
        self.assertIn("guestorders:1", _data(markup))
        second = self._open("guestorders:1")
        self.assertIn("2/2", second.message.edit_text.await_args.args[0])
