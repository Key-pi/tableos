"""What the Admin start page counts for a venue (admin step 2, PQ-050).

Money first: transfers nobody confirmed, guests waiting for their bill,
money on receipt not recorded, new orders not taken, parcels not sent; then
stock and the bot; and what a new venue still has to set up. Every count is
of this venue only."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.analytics import dashboard
from apps.billing.models import BillingRequest
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuItem, MenuItemVariant
from apps.orders.models import Order
from apps.partners.models import BotInstance, OpeningHours, PartnerBotSettings
from apps.tables.models import Table, TableSession
from apps.users.models import GuestProfile, TelegramAccount, User
from tests.webapp.test_guest_api import make_bot, make_venue

PREPAID = Order.PaymentTiming.PREPAID
ON_RECEIPT = Order.PaymentTiming.ON_RECEIPT
_telegram_ids = iter(range(70_000, 80_000))


def guest(partner):
    account = TelegramAccount.objects.create(telegram_id=next(_telegram_ids))
    return GuestProfile.objects.create(partner=partner, telegram_account=account)


def order(partner, *, channel=Order.Channel.PICKUP, status=Order.Status.NEW, timing="",
          paid=False, minutes_ago=0):
    created = Order.objects.create(
        partner=partner, guest=guest(partner), channel=channel, status=status,
        payment_timing=timing, paid_at=timezone.now() if paid else None,
        subtotal_amount="80.00", total_amount="80.00",
    )
    if minutes_ago:
        Order.objects.filter(id=created.id).update(
            created_at=timezone.now() - timedelta(minutes=minutes_ago)
        )
    return created


class MoneyFirstTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        self.other = make_venue("Other")

    def test_transfers_nobody_confirmed(self):
        oldest = order(self.partner, timing=PREPAID, minutes_ago=40)
        order(self.partner, timing=PREPAID, minutes_ago=5)
        order(self.partner, timing=PREPAID, paid=True, status=Order.Status.ACCEPTED)
        order(self.partner, timing=PREPAID, status=Order.Status.CANCELED)
        order(self.other, timing=PREPAID)
        waiting = dashboard.transfers_to_confirm(self.partner.id)
        self.assertEqual(waiting.count, 2)
        oldest.refresh_from_db()
        self.assertEqual(waiting.since, oldest.created_at)
        self.assertEqual(dashboard.transfer_wait_minutes(self.partner.id), 60)

    def test_new_orders_leave_transfers_to_their_own_line(self):
        order(self.partner, channel=Order.Channel.TABLE)
        order(self.partner, timing=ON_RECEIPT)
        order(self.partner, timing=PREPAID)
        order(self.partner, channel=Order.Channel.TABLE, status=Order.Status.ACCEPTED)
        order(self.other, channel=Order.Channel.TABLE)
        self.assertEqual(dashboard.new_orders(self.partner.id).count, 2)

    def test_money_on_receipt_not_recorded(self):
        order(self.partner, channel=Order.Channel.DELIVERY, timing=ON_RECEIPT,
              status=Order.Status.DELIVERING)
        order(self.partner, channel=Order.Channel.SHIPPING, timing=ON_RECEIPT,
              status=Order.Status.COMPLETED)
        # Not with the guest yet, already paid, or canceled: nothing to record.
        order(self.partner, channel=Order.Channel.DELIVERY, timing=ON_RECEIPT,
              status=Order.Status.PREPARING)
        order(self.partner, channel=Order.Channel.DELIVERY, timing=ON_RECEIPT,
              status=Order.Status.DELIVERING, paid=True)
        order(self.partner, channel=Order.Channel.PICKUP, timing=ON_RECEIPT,
              status=Order.Status.CANCELED)
        order(self.other, channel=Order.Channel.DELIVERY, timing=ON_RECEIPT,
              status=Order.Status.DELIVERING)
        self.assertEqual(dashboard.receipts_to_record(self.partner.id).count, 2)

    def test_parcels_not_sent(self):
        for status in (Order.Status.ACCEPTED, Order.Status.PREPARING, Order.Status.READY):
            order(self.partner, channel=Order.Channel.SHIPPING, timing=ON_RECEIPT, status=status)
        order(self.partner, channel=Order.Channel.SHIPPING, timing=ON_RECEIPT,
              status=Order.Status.DELIVERING)
        order(self.partner, channel=Order.Channel.PICKUP, timing=ON_RECEIPT,
              status=Order.Status.READY)
        order(self.other, channel=Order.Channel.SHIPPING, timing=ON_RECEIPT,
              status=Order.Status.READY)
        self.assertEqual(dashboard.parcels_to_send(self.partner.id).count, 3)

    def test_guests_waiting_for_their_bill(self):
        table = Table.objects.create(partner=self.partner, number=1, qr_token="dash0001")
        guest_profile = guest(self.partner)
        session = TableSession.objects.create(
            partner=self.partner, table=table, guest=guest_profile
        )
        for status in BillingRequest.Status:
            BillingRequest.objects.create(
                partner=self.partner, table=table, guest=guest_profile, table_session=session,
                request_type=BillingRequest.RequestType.PERSONAL, status=status,
            )
        self.assertEqual(dashboard.guests_waiting_for_bill(self.partner.id).count, 2)
        self.assertEqual(dashboard.guests_waiting_for_bill(self.other.id).count, 0)


class StockAndBotTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Shop")
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_stock_enabled=True, module_options_enabled=True
        )
        self.category = MenuItem.objects.filter(partner=self.partner).first().category

    def item(self, name, quantity, *, track=True, variants=()):
        item = MenuItem.objects.create(
            partner=self.partner, category=self.category, name=name, price="10.00",
            track_stock=track, stock_quantity=quantity,
        )
        for index, (amount, shown) in enumerate(variants):
            MenuItemVariant.objects.create(
                item=item, name=f"V{index}", price="10.00", stock_quantity=amount,
                is_available=shown, sort_order=index,
            )
        return item

    def test_out_low_and_below_zero(self):
        self.item("Out", 0)
        self.item("Below", -2)
        self.item("Low", 3)
        self.item("Plenty", 50)
        self.item("Not counted", 0, track=False)
        # Variants: out only when every shown one is out.
        self.item("Shirt out", 9, variants=[(0, True), (0, True), (7, False)])
        self.item("Shirt some", 0, variants=[(0, True), (4, True)])
        self.item("Shirt minus", 0, variants=[(-1, True), (20, True)])
        problems = dashboard.stock_problems(self.partner.id)
        self.assertEqual(problems.out, 3)  # Out, Below, Shirt out
        self.assertEqual(problems.below_zero, 2)  # Below, Shirt minus
        self.assertEqual(problems.low, 2)  # Low, Shirt some

    def test_without_options_the_item_count_is_what_is_sold(self):
        self.item("Shirt", 0, variants=[(5, True)])
        self.assertEqual(dashboard.stock_problems(self.partner.id).out, 0)
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_options_enabled=False
        )
        self.assertEqual(dashboard.stock_problems(self.partner.id).out, 1)

    def test_nothing_while_stock_is_off(self):
        self.item("Out", 0)
        PartnerBotSettings.objects.filter(partner=self.partner).update(module_stock_enabled=False)
        self.assertFalse(dashboard.stock_problems(self.partner.id))

    def test_a_bot_that_does_not_answer(self):
        self.assertIsNone(dashboard.broken_bot(self.partner.id))  # none yet: a setup step
        bot = make_bot(self.partner)
        self.assertEqual(dashboard.broken_bot(self.partner.id), bot)  # never started
        BotInstance.objects.filter(id=bot.id).update(
            runtime_state=BotInstance.RuntimeState.RUNNING
        )
        self.assertIsNone(dashboard.broken_bot(self.partner.id))
        BotInstance.objects.filter(id=bot.id).update(
            runtime_state=BotInstance.RuntimeState.TOKEN_REJECTED
        )
        self.assertEqual(dashboard.broken_bot(self.partner.id), bot)
        BotInstance.objects.filter(id=bot.id).update(
            runtime_state=BotInstance.RuntimeState.RUNNING, is_active=False
        )
        self.assertEqual(dashboard.broken_bot(self.partner.id), bot)


class SetupStepTests(TestCase):
    def setUp(self):
        self.partner = make_venue("New")

    def test_a_new_venue_has_steps_left(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            transfer_payment_details="", shipping_pay_transfer=False,
            shipping_pay_on_receipt=False,
        )
        MenuItem.objects.filter(partner=self.partner).delete()
        self.assertEqual(dashboard.setup_steps(self.partner.id), [
            dashboard.SETUP_BOT, dashboard.SETUP_MENU, dashboard.SETUP_PAYMENT_DETAILS,
            dashboard.SETUP_PAYMENT_OPTIONS, dashboard.SETUP_HOURS, dashboard.SETUP_TABLES,
            dashboard.SETUP_STAFF, dashboard.SETUP_CARD,
        ])

    def test_a_set_up_venue_has_none(self):
        make_bot(self.partner)
        OpeningHours.objects.create(
            partner=self.partner, weekday=0, opens_at="09:00", closes_at="21:00"
        )
        Table.objects.create(partner=self.partner, number=1, qr_token="setup001")
        user = User.objects.create_user(username="setup_staff", partner=self.partner,
                                        role=User.Role.WAITER)
        EmployeeProfile.objects.create(partner=self.partner, user=user)
        self.partner.address = "Київ, Поділ"
        self.partner.contact_phone = "+380441234567"
        self.partner.save()
        self.assertEqual(dashboard.setup_steps(self.partner.id), [])

    def test_steps_follow_the_modules(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_tables_enabled=False, module_pickup_enabled=False,
            module_delivery_enabled=False, module_shipping_enabled=False,
            transfer_payment_details="",
        )
        steps = dashboard.setup_steps(self.partner.id)
        for step in (dashboard.SETUP_TABLES, dashboard.SETUP_HOURS,
                     dashboard.SETUP_PAYMENT_DETAILS, dashboard.SETUP_PAYMENT_OPTIONS):
            with self.subTest(step):
                self.assertNotIn(step, steps)
