"""Pickup ordering with prepayment by transfer (docs/architecture/PICKUP.md)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.billing.models import Bill, BillAdjustment, Payment
from apps.billing.remote_orders import (
    RemoteOrderError,
    available_slots,
    awaits_transfer_check,
    confirm_transfer,
    get_remote_quote,
    place_remote_order,
    reject_transfer,
    save_guest_phone,
)
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.notifications.models import StaffNotification
from apps.orders.models import Cart, Order
from apps.orders.services import (
    OrderFlowError,
    add_items_to_cart_for_telegram_user,
    get_active_cart_for_telegram_user,
    switch_cart_channel_for_telegram_user,
    transition_order_status,
)
from apps.partners.models import OpeningHours, Partner, PartnerBotSettings
from apps.partners.opening_hours import describe_opening_hours, is_open_at
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import TelegramAccount, User
from apps.users.services import get_or_create_guest_profile

# Wednesday 2026-10-21 12:00 in Kyiv (UTC+3).
NOON = datetime(2026, 10, 21, 9, 0, tzinfo=dt_timezone.utc)
TG = 7300001


class PickupFixtureMixin:
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Pickup Venue",
            slug="pickup-venue",
            status=Partner.Status.ACTIVE,
            timezone="Europe/Kyiv",
        )
        self.settings = PartnerBotSettings.objects.create(
            partner=self.partner,
            module_menu_enabled=True,
            module_orders_enabled=True,
            module_cart_enabled=True,
            module_tables_enabled=True,
            module_pickup_enabled=True,
            transfer_payment_details="Карта 4149 0000 0000 0000, ФОП Тест",
        )
        category = MenuCategory.objects.create(partner=self.partner, name="Еда", sort_order=1)
        self.item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="PICK0001",
            name="Бургер",
            price="200.00",
            sort_order=1,
        )
        self.guest = get_or_create_guest_profile(partner_id=self.partner.id, telegram_id=TG)
        self.cashier = User.objects.create_user(
            username="pickup_cashier",
            password="x",
            partner=self.partner,
            role=User.Role.CASHIER,
        )
        staff_account = TelegramAccount.objects.create(telegram_id=7300901)
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.cashier,
            telegram_account=staff_account,
            bot_notifications_enabled=True,
        )

    def _cart(self, quantity=2):
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id,
            telegram_id=TG,
            raw_items=f"PICK0001x{quantity}",
        )

    def _place(self, *, slot="now", use_bonuses=False, total="400.00", to_pay="400.00", now=NOON):
        return place_remote_order(
            partner_id=self.partner.id,
            telegram_id=TG,
            slot=slot,
            use_bonuses=use_bonuses,
            expected_total=total,
            expected_to_pay=to_pay,
            now=now,
        )

    def _bill(self, order) -> Bill:
        return Bill.objects.get(bill_orders__order=order)

    def _seed_bonuses(self, amount="100.00"):
        BonusTransaction.objects.create(
            partner=self.partner,
            guest=self.guest,
            transaction_type=BonusTransaction.TransactionType.MANUAL,
            amount=amount,
        )
        self.guest.loyalty_balance = Decimal(amount)
        self.guest.save(update_fields=["loyalty_balance", "updated_at"])


class PickupCartTests(PickupFixtureMixin, TestCase):
    def test_cart_without_a_table_when_pickup_is_offered(self):
        self._cart()
        cart = get_active_cart_for_telegram_user(partner_id=self.partner.id, telegram_id=TG)
        self.assertIsNone(cart.table_session_id)
        self.assertEqual(cart.total_amount, Decimal("400.00"))

    def test_cart_without_a_table_is_refused_without_pickup(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(module_pickup_enabled=False)
        with self.assertRaisesMessage(OrderFlowError, "QR-кода стола"):
            self._cart()

    def test_pickup_cart_stays_pickup_at_a_table_until_switched(self):
        self._cart()
        table = Table.objects.create(partner=self.partner, number=3, qr_token="pickup-t3")
        session = activate_table_session(
            partner_id=self.partner.id, telegram_id=TG, payload=table.deep_link_payload
        )
        cart = get_active_cart_for_telegram_user(partner_id=self.partner.id, telegram_id=TG)
        self.assertEqual(cart.channel, Order.Channel.PICKUP)
        self.assertIsNone(cart.table_session_id)

        cart, removed = switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, channel=Order.Channel.TABLE
        )
        self.assertEqual(cart.table_session_id, session.id)
        self.assertEqual(cart.total_amount, Decimal("400.00"))
        self.assertEqual(removed, [])

    def test_switching_flow_removes_items_not_sold_there(self):
        hookah = MenuItem.objects.create(
            partner=self.partner,
            category=self.item.category,
            public_id="PICKHOOK",
            name="Кальян",
            price="500.00",
            available_for_pickup=False,
        )
        table = Table.objects.create(partner=self.partner, number=4, qr_token="pickup-t4")
        activate_table_session(
            partner_id=self.partner.id, telegram_id=TG, payload=table.deep_link_payload
        )
        self._cart()
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, raw_items=f"{hookah.public_id}x1"
        )

        cart, removed = switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, channel=Order.Channel.PICKUP
        )

        self.assertEqual(removed, ["Кальян"])
        self.assertEqual(cart.channel, Order.Channel.PICKUP)
        self.assertEqual(cart.total_amount, Decimal("400.00"))
        with self.assertRaisesMessage(OrderFlowError, "Недоступно для заказа на самовывоз"):
            add_items_to_cart_for_telegram_user(
                partner_id=self.partner.id, telegram_id=TG, raw_items=f"{hookah.public_id}x1"
            )

    def test_table_flow_requires_a_table(self):
        with self.assertRaisesMessage(OrderFlowError, "QR-код стола"):
            switch_cart_channel_for_telegram_user(
                partner_id=self.partner.id, telegram_id=TG, channel=Order.Channel.TABLE
            )


class OpeningHoursTests(PickupFixtureMixin, TestCase):
    def _hours(self, weekday, opens, closes):
        OpeningHours.objects.create(
            partner=self.partner, weekday=weekday, opens_at=opens, closes_at=closes
        )

    def test_no_hours_means_always_open(self):
        self.assertTrue(is_open_at(self.partner, NOON))
        self.assertEqual(available_slots(self.partner, NOON), ("now", "30", "60"))

    def test_daytime_interval_in_venue_timezone(self):
        self._hours(2, time(10, 0), time(12, 45))  # Wednesday, Kyiv time
        self.assertTrue(is_open_at(self.partner, NOON))
        self.assertFalse(is_open_at(self.partner, NOON + timedelta(hours=1)))
        # 12:30 is still open, 13:00 is not.
        self.assertEqual(available_slots(self.partner, NOON), ("now", "30"))

    def test_overnight_interval_covers_the_next_morning(self):
        self._hours(1, time(18, 0), time(3, 0))  # Tuesday 18:00 - Wednesday 03:00
        wednesday_0130_kyiv = datetime(2026, 10, 20, 22, 30, tzinfo=dt_timezone.utc)
        self.assertTrue(is_open_at(self.partner, wednesday_0130_kyiv))
        self.assertFalse(is_open_at(self.partner, NOON))
        self.assertEqual(available_slots(self.partner, NOON), ())

    def test_description(self):
        self._hours(0, time(10, 0), time(22, 0))
        self._hours(4, time(18, 0), time(3, 0))
        self.assertEqual(describe_opening_hours(self.partner), "Пн 10:00–22:00; Пт 18:00–03:00")


class PickupPlacementTests(PickupFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        save_guest_phone(partner_id=self.partner.id, telegram_id=TG, phone="+380501112233")
        self._cart()

    def test_quote_shows_bonus_option_and_payment_details(self):
        self._seed_bonuses("100.00")
        BonusProgram.objects.create(
            partner=self.partner,
            name="Cashback",
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00",
            max_redeem_share="20.00",
        )
        with patch("apps.billing.remote_orders.timezone.now", return_value=NOON):
            quote = get_remote_quote(partner_id=self.partner.id, telegram_id=TG, use_bonuses=True)
        self.assertEqual(quote.total, Decimal("400.00"))
        self.assertEqual(quote.bonus_to_spend, Decimal("80.00"))
        self.assertEqual(quote.to_pay, Decimal("320.00"))
        self.assertIn("ФОП Тест", quote.payment_details)

    def test_order_waits_for_payment_check(self):
        order = self._place(slot="30")

        self.assertEqual(order.channel, Order.Channel.PICKUP)
        self.assertIsNone(order.table_id)
        self.assertEqual(order.requested_for, NOON + timedelta(minutes=30))
        self.assertTrue(awaits_transfer_check(order))
        bill = self._bill(order)
        self.assertEqual(bill.status, Bill.Status.ISSUED)
        self.assertEqual(bill.total_amount, Decimal("400.00"))
        self.assertFalse(Cart.objects.filter(guest=self.guest, status=Cart.Status.ACTIVE).exists())
        notification = StaffNotification.objects.get(order=order)
        self.assertIn("самовывоз, к 12:30", notification.message)
        self.assertIn("+380501112233", notification.message)
        self.assertIn("перевод", notification.message.lower())

    def test_changed_cart_creates_nothing(self):
        with self.assertRaisesMessage(RemoteOrderError, "Корзина изменилась"):
            self._place(total="200.00", to_pay="200.00")
        self.assertFalse(Order.objects.exists())
        self.assertTrue(Cart.objects.filter(guest=self.guest, status=Cart.Status.ACTIVE).exists())

    def test_phone_is_required(self):
        type(self.guest).objects.filter(id=self.guest.id).update(phone="")
        with self.assertRaisesMessage(RemoteOrderError, "номером телефона"):
            self._place()

    def test_payment_details_are_required(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(transfer_payment_details="")
        with self.assertRaisesMessage(RemoteOrderError, "реквизиты"):
            self._place()

    def test_closed_venue_refuses_with_hours(self):
        OpeningHours.objects.create(
            partner=self.partner, weekday=2, opens_at=time(18, 0), closes_at=time(23, 0)
        )
        with self.assertRaisesMessage(RemoteOrderError, "Ср 18:00–23:00"):
            self._place()
        self.assertFalse(Order.objects.exists())

    def test_partial_bonus_payment_reduces_the_transfer(self):
        self._seed_bonuses("100.00")
        BonusProgram.objects.create(
            partner=self.partner,
            name="Cashback",
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00",
            max_redeem_share="20.00",
        )
        order = self._place(use_bonuses=True, to_pay="320.00")
        bill = self._bill(order)
        self.assertEqual(bill.bonus_spent_amount, Decimal("80.00"))
        self.assertEqual(bill.total_amount, Decimal("320.00"))
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.loyalty_balance, Decimal("20.00"))

    def test_full_bonus_payment_needs_no_transfer(self):
        self._seed_bonuses("500.00")
        BonusProgram.objects.create(
            partner=self.partner,
            name="Cashback",
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00",
            max_redeem_share="100.00",
        )
        order = self._place(use_bonuses=True, to_pay="0.00")
        self.assertIsNotNone(order.paid_at)
        self.assertFalse(awaits_transfer_check(order))
        self.assertIn("бонусами полностью", StaffNotification.objects.get(order=order).message)


class PickupPaymentCheckTests(PickupFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        save_guest_phone(partner_id=self.partner.id, telegram_id=TG, phone="+380501112233")
        self._cart()

    def test_unpaid_pickup_order_cannot_be_accepted_directly(self):
        order = self._place()
        with self.assertRaisesMessage(OrderFlowError, "подтвердите оплату"):
            transition_order_status(order=order, to_status=Order.Status.ACCEPTED)

    def test_confirmed_transfer_takes_the_order_into_work(self):
        BonusProgram.objects.create(
            partner=self.partner,
            name="Cashback",
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00",
        )
        order = self._place()
        order = confirm_transfer(order=order, actor=self.cashier)

        self.assertEqual(order.status, Order.Status.ACCEPTED)
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(order.payment_method, Order.PaymentMethod.TRANSFER)
        bill = self._bill(order)
        self.assertEqual(bill.status, Bill.Status.PAID)
        self.assertEqual(
            bill.payments.get().method,
            Payment.Method.TRANSFER,
        )
        self.assertTrue(
            BonusTransaction.objects.filter(
                order=order, transaction_type=BonusTransaction.TransactionType.ACCRUAL
            ).exists()
        )
        with self.assertRaisesMessage(RemoteOrderError, "уже обработана"):
            confirm_transfer(order=order, actor=self.cashier)

    def test_only_cash_handling_roles_check_payments(self):
        waiter = User.objects.create_user(
            username="pickup_waiter", password="x", partner=self.partner, role=User.Role.WAITER
        )
        order = self._place()
        with self.assertRaisesMessage(RemoteOrderError, "владелец, менеджер или кассир"):
            confirm_transfer(order=order, actor=waiter)
        other = Partner.objects.create(name="Other", slug="other-pickup")
        foreign = User.objects.create_user(
            username="foreign_cashier", password="x", partner=other, role=User.Role.CASHIER
        )
        with self.assertRaises(RemoteOrderError):
            reject_transfer(order=order, actor=foreign)

    def test_missing_transfer_cancels_and_returns_bonuses(self):
        self._seed_bonuses("100.00")
        BonusProgram.objects.create(
            partner=self.partner,
            name="Cashback",
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00",
            max_redeem_share="20.00",
        )
        order = self._place(use_bonuses=True, to_pay="320.00")
        bill = self._bill(order)
        order = reject_transfer(order=order, actor=self.cashier)

        self.assertEqual(order.status, Order.Status.CANCELED)
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.CANCELED)
        adjustment = BillAdjustment.objects.get(bill=bill)
        self.assertEqual(adjustment.reason_code, BillAdjustment.ReasonCode.PAYMENT_NOT_RECEIVED)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.loyalty_balance, Decimal("100.00"))


class ChannelReportTests(PickupFixtureMixin, TestCase):
    """ADR-0009: the day report splits orders and money by ordering channel."""

    def test_day_report_shows_pickup_separately(self):
        from apps.analytics.services import build_daily_operations_report

        save_guest_phone(partner_id=self.partner.id, telegram_id=TG, phone="+380501112233")
        self._cart()
        order = self._place()
        confirm_transfer(order=order, actor=self.cashier)

        report = build_daily_operations_report(partner_id=self.partner.id)

        pickup = next(item for item in report.channel_breakdown if item["channel"] == "pickup")
        self.assertEqual(pickup["orders_count"], 1)
        self.assertEqual(pickup["revenue"], Decimal("400.00"))
        self.assertFalse([item for item in report.channel_breakdown if item["channel"] == "table"])
