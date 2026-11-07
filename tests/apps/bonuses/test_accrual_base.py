"""Cashback accrual base: full total or money paid (PQ-030, B3.2 part C)."""

from decimal import Decimal

from django.test import TestCase

from apps.billing.models import Bill, Payment
from apps.billing.services import (
    _bill_bonus_share_by_order,
    create_bill_from_orders,
    record_payment,
    redeem_bonus_for_bill,
)
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.bonuses.services import (
    BonusServiceError,
    apply_bonus_programs,
    preview_walk_in_sale_by_customer_code,
    register_walk_in_sale,
)
from apps.bonuses.strategies import BonusContext, _cashback_bonus
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.services import create_order_from_session
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import GuestProfile, TelegramAccount

MONEY_PAID = BonusProgram.AccrualBase.MONEY_PAID
FULL_TOTAL = BonusProgram.AccrualBase.FULL_TOTAL


def _seed_balance(guest, amount):
    BonusTransaction.objects.create(
        partner=guest.partner,
        guest=guest,
        transaction_type=BonusTransaction.TransactionType.MANUAL,
        amount=amount,
        comment="Seeded test balance",
    )
    guest.loyalty_balance = Decimal(amount)
    guest.save(update_fields=["loyalty_balance", "updated_at"])


class CashbackStrategyTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Base Venue",
            slug="base-venue",
            status=Partner.Status.ACTIVE,
        )
        account = TelegramAccount.objects.create(telegram_id=7700001)
        self.guest = GuestProfile.objects.create(partner=self.partner, telegram_account=account)

    def _program(self, accrual_base, **extra):
        return BonusProgram(
            partner=self.partner,
            name="Cashback",
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent=Decimal("10.00"),
            accrual_base=accrual_base,
            **extra,
        )

    def _context(self, total, bonus_paid):
        return BonusContext(
            event=BonusProgram.TriggerEvent.ORDER_COMPLETED,
            guest=self.guest,
            purchase_total=Decimal(total),
            bonus_paid_amount=Decimal(bonus_paid),
        )

    def test_default_base_is_full_total(self):
        program = BonusProgram.objects.create(
            partner=self.partner,
            name="Legacy",
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent=Decimal("10.00"),
        )
        self.assertEqual(program.accrual_base, FULL_TOTAL)
        self.assertEqual(_cashback_bonus(program, self._context("300", "90")), Decimal("30.00"))

    def test_money_paid_subtracts_bonus_paid_part(self):
        program = self._program(MONEY_PAID)
        self.assertEqual(_cashback_bonus(program, self._context("300", "90")), Decimal("21.00"))

    def test_money_paid_never_goes_negative(self):
        program = self._program(MONEY_PAID)
        self.assertEqual(_cashback_bonus(program, self._context("100", "150")), Decimal("0.00"))

    def test_minimum_order_total_uses_full_total(self):
        program = self._program(MONEY_PAID, min_order_total=Decimal("250.00"))
        # 300 passes the minimum even though only 210 is paid with money.
        self.assertEqual(_cashback_bonus(program, self._context("300", "90")), Decimal("21.00"))

    def test_apply_bonus_programs_rejects_negative_bonus_paid(self):
        with self.assertRaisesMessage(BonusServiceError, "cannot be negative"):
            apply_bonus_programs(
                partner_id=self.partner.id,
                event=BonusProgram.TriggerEvent.MANUAL_PURCHASE,
                guest=self.guest,
                purchase_total=Decimal("100.00"),
                bonus_paid_amount=Decimal("-1.00"),
            )

    def test_visit_programme_ignores_accrual_base(self):
        program = BonusProgram.objects.create(
            partner=self.partner,
            name="Visit",
            trigger_event=BonusProgram.TriggerEvent.MANUAL_PURCHASE,
            program_type=BonusProgram.ProgramType.VISIT,
            fixed_amount="20.00",
            accrual_base=MONEY_PAID,
            config={"once_per_day": False},
        )
        transactions = apply_bonus_programs(
            partner_id=self.partner.id,
            event=BonusProgram.TriggerEvent.MANUAL_PURCHASE,
            guest=self.guest,
            purchase_total=Decimal("100.00"),
            bonus_paid_amount=Decimal("100.00"),
        )
        self.assertEqual([t.amount for t in transactions], [Decimal("20.00")])
        self.assertEqual(transactions[0].program, program)


class PaidOrderAccrualBaseTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Order Base Venue",
            slug="order-base-venue",
            status=Partner.Status.ACTIVE,
        )
        PartnerBotSettings.objects.create(partner=self.partner, module_billing_enabled=True)
        table = Table.objects.create(
            partner=self.partner,
            number=1,
            name="Table 1",
            qr_token="base_tbl_01",
        )
        category = MenuCategory.objects.create(partner=self.partner, name="Food", sort_order=1)
        self.item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="BASE0001",
            name="Soup",
            price="100.00",
            sort_order=1,
        )
        self.session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=7700101,
            payload=table.deep_link_payload,
            username="base_guest",
            first_name="Base",
        )
        self.guest = self.session.guest
        _seed_balance(self.guest, "500.00")
        self.program = BonusProgram.objects.create(
            partner=self.partner,
            name="Cashback",
            trigger_event=BonusProgram.TriggerEvent.ORDER_COMPLETED,
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="10.00",
            max_redeem_share="100.00",
        )

    def _order(self, quantity):
        return create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[
                {
                    "menu_item_id": self.item.id,
                    "item_name": self.item.name,
                    "unit_price": self.item.price,
                    "quantity": quantity,
                }
            ],
        )

    def _pay(self, orders, bonus):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(order.id) for order in orders],
            kind=Bill.Kind.PERSONAL,
        )
        redeem_bonus_for_bill(bill=bill, amount=bonus)
        bill.refresh_from_db()
        record_payment(bill=bill, amount=bill.total_amount, method=Payment.Method.CASH)
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.PAID)
        return bill

    def _accruals(self):
        return {
            transaction.order_id: transaction.amount
            for transaction in BonusTransaction.objects.filter(
                partner=self.partner,
                transaction_type=BonusTransaction.TransactionType.ACCRUAL,
                order__isnull=False,
            )
        }

    def test_full_total_base_keeps_accruing_on_bonus_paid_part(self):
        order = self._order(3)
        self._pay([order], "90.00")
        self.assertEqual(self._accruals(), {order.id: Decimal("30.00")})

    def test_money_paid_base_accrues_only_on_money(self):
        self.program.accrual_base = MONEY_PAID
        self.program.save(update_fields=["accrual_base", "updated_at"])
        order = self._order(3)
        self._pay([order], "90.00")
        self.assertEqual(self._accruals(), {order.id: Decimal("21.00")})

    def test_money_paid_base_splits_bonuses_between_orders(self):
        self.program.accrual_base = MONEY_PAID
        self.program.save(update_fields=["accrual_base", "updated_at"])
        small = self._order(1)
        large = self._order(2)
        self._pay([small, large], "90.00")
        # Shares 30 and 60: money parts 70 and 140.
        self.assertEqual(
            self._accruals(),
            {small.id: Decimal("7.00"), large.id: Decimal("14.00")},
        )

    def test_bonus_shares_add_up_with_remainder_on_last_order(self):
        orders = [self._order(1) for _ in range(3)]
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(order.id) for order in orders],
            kind=Bill.Kind.PERSONAL,
        )
        redeem_bonus_for_bill(bill=bill, amount="100.00")
        bill.refresh_from_db()

        shares = _bill_bonus_share_by_order(bill)

        self.assertEqual(sum(shares.values()), Decimal("100.00"))
        ordered = [shares[order_id] for order_id in sorted(shares, key=str)]
        self.assertEqual(ordered, [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")])

    def test_bill_without_bonuses_has_no_shares(self):
        order = self._order(1)
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(order.id)],
            kind=Bill.Kind.PERSONAL,
        )
        self.assertEqual(_bill_bonus_share_by_order(bill), {})


class WalkInSaleAccrualBaseTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Walk Base Venue",
            slug="walk-base-venue",
            status=Partner.Status.ACTIVE,
        )
        account = TelegramAccount.objects.create(telegram_id=7700201)
        self.guest = GuestProfile.objects.create(partner=self.partner, telegram_account=account)
        _seed_balance(self.guest, "250.00")
        self.program = BonusProgram.objects.create(
            partner=self.partner,
            name="Walk-in cashback",
            trigger_event=BonusProgram.TriggerEvent.MANUAL_PURCHASE,
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="10.00",
            max_redeem_share="30.00",
            accrual_base=MONEY_PAID,
        )
        category = MenuCategory.objects.create(partner=self.partner, name="Bar", sort_order=1)
        self.coffee = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            name="Coffee",
            price="100.00",
            sort_order=1,
        )

    def test_sale_with_redemption_accrues_on_money_part(self):
        sale = register_walk_in_sale(
            partner_id=self.partner.id,
            guest=self.guest,
            amount="300.00",
            redeem_bonus=True,
        )
        self.assertEqual(sale.bonus_spent_amount, Decimal("90.00"))
        self.assertEqual(sale.bonus_awarded_amount, Decimal("21.00"))

    def test_sale_without_redemption_accrues_on_full_amount(self):
        sale = register_walk_in_sale(
            partner_id=self.partner.id,
            guest=self.guest,
            amount="300.00",
        )
        self.assertEqual(sale.bonus_awarded_amount, Decimal("30.00"))

    def test_full_total_programme_ignores_redemption(self):
        self.program.accrual_base = FULL_TOTAL
        self.program.save(update_fields=["accrual_base", "updated_at"])
        sale = register_walk_in_sale(
            partner_id=self.partner.id,
            guest=self.guest,
            amount="300.00",
            redeem_bonus=True,
        )
        self.assertEqual(sale.bonus_spent_amount, Decimal("90.00"))
        self.assertEqual(sale.bonus_awarded_amount, Decimal("30.00"))

    def test_preview_projects_accrual_for_the_chosen_redemption(self):
        items = [{"menu_item_id": str(self.coffee.id), "quantity": 3}]
        plain = preview_walk_in_sale_by_customer_code(
            partner_id=self.partner.id,
            customer_code=self.guest.customer_code,
            items=items,
        )
        redeemed = preview_walk_in_sale_by_customer_code(
            partner_id=self.partner.id,
            customer_code=self.guest.customer_code,
            items=items,
            redeem_bonus=True,
        )
        self.assertEqual(plain.projected_bonus_amount, Decimal("30.00"))
        self.assertEqual(redeemed.redeemable_bonus_amount, Decimal("90.00"))
        self.assertEqual(redeemed.projected_bonus_amount, Decimal("21.00"))
