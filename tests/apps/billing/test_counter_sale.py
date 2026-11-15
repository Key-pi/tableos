"""A sale at the counter is an order channel (PQ-054, owner 2026-11-15).

One action of the seller makes an order «На кассе», its bill and the payment
by the chosen method — cash, card (terminal), or part bonuses when the guest
named their code. The sale is handed over and paid at once and never goes to
the kitchen and bar screens. Without a code the guest is the sale's
anonymous guest.
"""

from decimal import Decimal
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, connections
from django.test import TestCase, TransactionTestCase

from apps.analytics.services import build_daily_operations_report
from apps.billing import counter_sales
from apps.billing.counter_sales import CounterSaleError, preview_counter_sale, sell_at_counter
from apps.billing.models import Bill, Payment
from apps.billing.services import refund_bill_lines
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.bonuses.services import sync_bonus_balance_from_ledger
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem, StockMovement
from apps.notifications.models import StaffNotification
from apps.orders.models import Order
from apps.orders.services import OrderFlowError, switch_cart_channel_for_telegram_user
from apps.partners.models import Partner, PartnerBotSettings
from apps.users.models import GuestProfile, TelegramAccount, User


def make_venue(slug="counter-venue"):
    partner = Partner.objects.create(name="Counter", slug=slug, status=Partner.Status.ACTIVE)
    PartnerBotSettings.objects.create(
        partner=partner, module_menu_enabled=True, module_quick_sale_enabled=True,
        module_reports_enabled=True,
    )
    category = MenuCategory.objects.create(partner=partner, name="Табак")
    tobacco = MenuItem.objects.create(
        partner=partner, category=category, name="Табак Darkside", price="250.00"
    )
    return partner, tobacco


def make_staff(partner, role=User.Role.CASHIER, telegram_id=7701001, name="seller"):
    user = User.objects.create_user(
        username=f"{name}-{partner.slug}", password="x", partner=partner, role=role,
    )
    return EmployeeProfile.objects.create(
        partner=partner, user=user,
        telegram_account=TelegramAccount.objects.get_or_create(telegram_id=telegram_id)[0],
    )


def make_guest(partner, telegram_id=7702001, balance=None):
    guest = GuestProfile.objects.create(
        partner=partner,
        telegram_account=TelegramAccount.objects.get_or_create(telegram_id=telegram_id)[0],
    )
    if balance is not None:
        BonusTransaction.objects.create(
            partner=partner, guest=guest, transaction_type="manual", amount=Decimal(balance),
            source_type="test", source_id="seed", idempotency_key=f"seed:{guest.id}",
        )
        sync_bonus_balance_from_ledger(guest)
    return guest


def programme(partner, event, percent="5", redeem_share="50"):
    return BonusProgram.objects.create(
        partner=partner, name=f"{event} {percent}%", trigger_event=event,
        program_type=BonusProgram.ProgramType.CASHBACK, percent=Decimal(percent),
        max_redeem_share=Decimal(redeem_share), is_active=True,
    )


class CounterFixture(TestCase):
    def setUp(self):
        self.partner, self.tobacco = make_venue()
        self.seller = make_staff(self.partner)

    def lines(self, quantity=2):
        return [{"menu_item_id": self.tobacco.id, "quantity": quantity}]

    def sell(self, **kwargs):
        options = {"partner_id": self.partner.id, "seller": self.seller, "customer_code": "",
                   "items": self.lines(), "payment": Payment.Method.CASH}
        options.update(kwargs)
        return sell_at_counter(**options)


class CounterSaleTests(CounterFixture):
    def test_an_anonymous_cash_sale_is_an_order_a_bill_and_a_payment(self):
        notifications = StaffNotification.objects.count()
        sale = self.sell()

        order = Order.objects.get()
        self.assertEqual(sale.order, order)
        self.assertEqual(
            (order.channel, order.status, order.total_amount, order.payment_method),
            (Order.Channel.COUNTER, Order.Status.COMPLETED, Decimal("500.00"),
             Order.PaymentMethod.CASH),
        )
        self.assertEqual(order.assigned_employee, self.seller)
        self.assertIsNotNone(order.received_at)
        self.assertIsNotNone(order.paid_at)
        self.assertIsNone(order.guest.telegram_account_id)  # the sale's anonymous guest
        bill = Bill.objects.get()
        self.assertEqual((bill.status, bill.total_amount), (Bill.Status.PAID, Decimal("500.00")))
        payment = Payment.objects.get()
        self.assertEqual(
            (payment.method, payment.amount, payment.created_by),
            (Payment.Method.CASH, Decimal("500.00"), self.seller.user),
        )
        # Handed over at once: nobody is told about a new order.
        self.assertEqual(StaffNotification.objects.count(), notifications)
        self.assertFalse(sale.repeated)

    def test_by_card(self):
        self.sell(payment=Payment.Method.TERMINAL)
        self.assertEqual(Payment.objects.get().method, Payment.Method.TERMINAL)
        self.assertEqual(Order.objects.get().payment_method, Order.PaymentMethod.TERMINAL)

    def test_each_anonymous_sale_has_its_own_guest(self):
        self.sell()
        self.sell()
        self.assertEqual(
            Order.objects.values("guest").distinct().count(), 2
        )

    def test_the_method_must_be_cash_or_card(self):
        for payment in ("", Payment.Method.TRANSFER, Payment.Method.BONUSES, "gold"):
            with self.subTest(payment=payment), self.assertRaises(CounterSaleError):
                self.sell(payment=payment)
        self.assertFalse(Order.objects.exists())

    def test_a_changed_amount_is_refused_and_nothing_is_saved(self):
        with self.assertRaises(CounterSaleError):
            self.sell(expected_to_pay="450.00")
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Bill.objects.exists())
        self.sell(expected_to_pay="500.00")
        self.assertEqual(Order.objects.count(), 1)

    def test_a_repeated_key_returns_the_sale_already_made(self):
        first = self.sell(request_key="till-key-0001")
        again = self.sell(request_key="till-key-0001")
        self.assertEqual(again.order, first.order)
        self.assertTrue(again.repeated)
        self.assertEqual((Order.objects.count(), Payment.objects.count()), (1, 1))

    def test_an_unknown_code_is_refused(self):
        with self.assertRaises(CounterSaleError):
            self.sell(customer_code="NOPE00")

    def test_the_comment_is_kept(self):
        self.assertEqual(self.sell(comment="  без пакета ").order.comment, "без пакета")

    def test_no_lines_is_refused(self):
        with self.assertRaises(CounterSaleError):
            self.sell(items=[])

    def test_the_stock_may_go_below_zero(self):
        MenuItem.objects.filter(id=self.tobacco.id).update(track_stock=True, stock_quantity=1)
        PartnerBotSettings.objects.filter(partner=self.partner).update(module_stock_enabled=True)
        self.sell(items=self.lines(3))
        self.tobacco.refresh_from_db()
        self.assertEqual(self.tobacco.stock_quantity, -2)
        self.assertEqual(StockMovement.objects.get().reason, StockMovement.Reason.QUICK_SALE)

    def test_a_refund_works_as_for_any_bill(self):
        sale = self.sell()
        owner = make_staff(self.partner, role=User.Role.OWNER, telegram_id=7701009, name="own")
        refund = refund_bill_lines(
            bill=sale.bill, bill_item_ids=list(sale.bill.items.values_list("id", flat=True)),
            actor=owner.user, reason_code="input_error", method="cash",
        )
        self.assertEqual(refund.money_amount, Decimal("500.00"))


class CounterBonusTests(CounterFixture):
    def setUp(self):
        super().setUp()
        # Cashback at the counter follows the «Покупка на кассе» programmes,
        # as the quick sale did; paid orders of other channels have theirs.
        programme(self.partner, BonusProgram.TriggerEvent.MANUAL_PURCHASE, percent="5")
        programme(self.partner, BonusProgram.TriggerEvent.ORDER_COMPLETED, percent="10")
        self.guest = make_guest(self.partner, balance="100.00")

    def test_the_named_guest_earns_by_the_counter_programme(self):
        sale = self.sell(customer_code=self.guest.customer_code.lower())
        self.assertEqual(sale.order.guest, self.guest)
        self.assertEqual(sale.bonus_awarded, Decimal("25.00"))  # 5 % of 500
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.loyalty_balance, Decimal("125.00"))

    def test_part_bonuses_and_the_rest_in_cash(self):
        preview = preview_counter_sale(
            partner_id=self.partner.id, customer_code=self.guest.customer_code,
            items=self.lines(), use_bonuses=True,
        )
        self.assertEqual((preview.total, preview.redeemable, preview.to_pay),
                         (Decimal("500.00"), Decimal("100.00"), Decimal("400.00")))
        sale = self.sell(customer_code=self.guest.customer_code, use_bonuses=True,
                         expected_to_pay=preview.to_pay)
        payments = {p.method: p.amount for p in Payment.objects.filter(bill=sale.bill)}
        self.assertEqual(payments, {Payment.Method.CASH: Decimal("400.00")})
        self.assertEqual(sale.bill.bonus_spent_amount, Decimal("100.00"))
        self.assertEqual(sale.order.payment_method, Order.PaymentMethod.MIXED)
        self.assertEqual(sale.bill.status, Bill.Status.PAID)

    def test_bonuses_covering_everything_need_no_method(self):
        BonusProgram.objects.filter(
            partner=self.partner, trigger_event=BonusProgram.TriggerEvent.MANUAL_PURCHASE
        ).update(max_redeem_share=Decimal("100"))
        MenuItem.objects.filter(id=self.tobacco.id).update(price="80.00")  # balance 100
        sale = self.sell(customer_code=self.guest.customer_code, use_bonuses=True,
                         items=[{"menu_item_id": self.tobacco.id, "quantity": 1}],
                         payment="")
        self.assertEqual(sale.bill.status, Bill.Status.PAID)
        self.assertFalse(Payment.objects.filter(bill=sale.bill).exclude(
            method=Payment.Method.BONUSES).exists())

    def test_the_anonymous_guest_spends_and_earns_nothing(self):
        sale = self.sell(use_bonuses=True)
        self.assertEqual(sale.bonus_awarded, Decimal("0.00"))
        self.assertEqual(sale.bill.bonus_spent_amount, Decimal("0.00"))


class CounterRightsTests(CounterFixture):
    def test_a_waiter_cannot_sell(self):
        waiter = make_staff(self.partner, role=User.Role.WAITER, telegram_id=7701002, name="w")
        with self.assertRaises(CounterSaleError):
            self.sell(seller=waiter)

    def test_an_inactive_seller_cannot_sell(self):
        EmployeeProfile.objects.filter(id=self.seller.id).update(is_active=False)
        with self.assertRaises(CounterSaleError):
            self.sell()

    def test_a_seller_of_another_venue_cannot_sell(self):
        other, _tobacco = make_venue("other-counter")
        stranger = make_staff(other, telegram_id=7701003, name="stranger")
        with self.assertRaises(CounterSaleError):
            self.sell(seller=stranger)

    def test_not_without_the_quick_sale_module(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_quick_sale_enabled=False
        )
        with self.assertRaises(CounterSaleError):
            self.sell()
        self.assertFalse(Order.objects.exists())

    def test_a_guest_cannot_pick_the_counter_as_a_way_to_order(self):
        guest = make_guest(self.partner)
        with self.assertRaises(OrderFlowError):
            switch_cart_channel_for_telegram_user(
                partner_id=self.partner.id, telegram_id=guest.telegram_account.telegram_id,
                channel=Order.Channel.COUNTER,
            )


class CounterReportTests(CounterFixture):
    def test_the_day_report_counts_the_sale_by_method_and_channel(self):
        self.sell(payment=Payment.Method.CASH)
        self.sell(payment=Payment.Method.TERMINAL, items=self.lines(1))
        report = build_daily_operations_report(partner_id=self.partner.id)
        self.assertEqual(report.payment_method_totals[Payment.Method.CASH], Decimal("500.00"))
        self.assertEqual(report.payment_method_totals[Payment.Method.TERMINAL], Decimal("250.00"))
        counter = next(row for row in report.channel_breakdown
                       if row["label"] == Order.Channel.COUNTER.label)
        self.assertEqual((counter["orders_count"], counter["revenue"]), (2, Decimal("750.00")))
        self.assertEqual(report.total_revenue, Decimal("750.00"))


@skipUnless(connection.vendor == "postgresql", "нужны построчные блокировки")
class CounterRepeatRaceTests(TransactionTestCase):
    def test_two_presses_at_once_make_one_sale(self):
        partner, tobacco = make_venue("race-counter")
        seller = make_staff(partner)
        guest = make_guest(partner, balance="100.00")
        programme(partner, BonusProgram.TriggerEvent.MANUAL_PURCHASE)
        barrier = Barrier(2)
        results, errors = [], []
        real_lines = counter_sales._lines

        def slow_lines(*args, **kwargs):
            barrier.wait()  # both copies are past the "already made?" check
            return real_lines(*args, **kwargs)

        def press():
            try:
                results.append(sell_at_counter(
                    partner_id=partner.id, seller=seller, customer_code=guest.customer_code,
                    items=[{"menu_item_id": tobacco.id, "quantity": 1}],
                    payment=Payment.Method.CASH, use_bonuses=True, request_key="race-key-0001",
                ))
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [Thread(target=press) for _ in range(2)]
        with patch.object(counter_sales, "_lines", slow_lines):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(Order.objects.filter(partner=partner).count(), 1)
        self.assertEqual({result.order.id for result in results},
                         {Order.objects.get(partner=partner).id})
        self.assertEqual(
            BonusTransaction.objects.filter(guest=guest, transaction_type="redemption").count(), 1
        )
