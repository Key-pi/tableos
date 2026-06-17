from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.billing.models import Bill, BillingRequest, Payment
from apps.billing.repositories import BillRepository, BillingRequestRepository
from apps.billing.services import (
    BillingServiceError,
    attach_orders_to_bill,
    cancel_billing_request,
    create_bill_from_orders,
    create_billing_request_for_telegram_user,
    create_personal_bills_for_table,
    create_shared_bill_for_table,
    issue_bill,
    mark_billing_request_processed,
    record_payment,
    redeem_bonus_for_bill,
)
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.notifications.models import StaffNotification
from apps.orders.models import Order
from apps.orders.services import create_order, create_order_from_session, transition_order_status
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import TelegramAccount, User


async def _successful_send(_token, messages):
    return [(chat_id, True, "") for chat_id, _text in messages]


class BillingServiceTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Billing Venue",
            slug="billing-venue",
            status=Partner.Status.ACTIVE,
        )
        PartnerBotSettings.objects.create(
            partner=self.partner,
            module_billing_enabled=True,
        )
        self.bot = BotInstance.objects.create(
            partner=self.partner,
            display_name="Billing Venue Bot",
            username="billing_venue_bot",
            mode=BotInstance.Mode.POLLING,
            is_active=True,
            token_encrypted="",
        )
        self.bot.set_token("323456789:ABCDEFGHIJKLMNOPQRST_uv-wxyz")
        self.bot.save(update_fields=["token_encrypted"])
        self.table = Table.objects.create(
            partner=self.partner,
            number=1,
            name="Table 1",
            qr_token="billing_tbl_01",
        )
        category = MenuCategory.objects.create(
            partner=self.partner,
            name="Напитки",
            sort_order=10,
        )
        self.menu_item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="BILDR001",
            name="Lemonade",
            price="150.00",
            sort_order=10,
        )
        self.session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=5550001,
            payload=self.table.deep_link_payload,
            username="billing_guest",
            first_name="Billing",
        )
        self.order = create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": 2,
                }
            ],
        )
        BonusProgram.objects.create(
            partner=self.partner,
            name="Billing cashback",
            trigger_event=BonusProgram.TriggerEvent.ORDER_COMPLETED,
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00",
            max_redeem_share="30.00",
            is_active=True,
        )
        self.session.guest.loyalty_balance = Decimal("250.00")
        self.session.guest.save(update_fields=["loyalty_balance", "updated_at"])
        manager_user = User.objects.create_user(
            username="billing_manager",
            password="pass12345",
            partner=self.partner,
            role=User.Role.MANAGER,
        )
        manager_account = TelegramAccount.objects.create(
            telegram_id=661001,
            username="billing_manager",
        )
        self.manager = EmployeeProfile.objects.create(
            partner=self.partner,
            user=manager_user,
            telegram_account=manager_account,
            bot_notifications_enabled=True,
            notify_on_billing_requests=True,
        )

    def test_create_bill_from_orders_builds_allocated_bill_items(self):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
            label="Guest 1",
            kind=Bill.Kind.PERSONAL,
        )

        self.assertEqual(bill.table_id, self.table.id)
        self.assertEqual(bill.primary_guest_id, self.session.guest_id)
        self.assertEqual(bill.bill_orders.count(), 1)
        self.assertEqual(bill.items.count(), 1)
        self.assertEqual(bill.total_amount, Decimal("300.00"))

    def test_create_bill_from_orders_allows_personal_bill_without_table(self):
        order = create_order(
            partner_id=self.partner.id,
            guest=self.session.guest,
            table=None,
            table_session=None,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": 1,
                }
            ],
            comment="Walk-in bill preparation",
        )

        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(order.id)],
            kind=Bill.Kind.PERSONAL,
            label="Off-table personal bill",
        )

        self.assertIsNone(bill.table_id)
        self.assertEqual(bill.primary_guest_id, self.session.guest_id)
        self.assertEqual(bill.total_amount, Decimal("150.00"))

    def test_attach_orders_to_bill_rejects_orders_from_another_table(self):
        second_table = Table.objects.create(
            partner=self.partner,
            number=2,
            name="Table 2",
            qr_token="billing_tbl_02",
        )
        second_session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=5550102,
            payload=second_table.deep_link_payload,
            username="billing_guest_second_table",
            first_name="Second Table",
        )
        second_order = create_order_from_session(
            partner_id=self.partner.id,
            table_session=second_session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": 1,
                }
            ],
        )
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )

        with self.assertRaisesMessage(
            BillingServiceError,
            "В этот счёт можно добавлять только заказы того же стола.",
        ):
            attach_orders_to_bill(
                bill=bill,
                order_ids=[str(second_order.id)],
            )

    def test_record_payment_supports_partial_and_full_payment(self):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )
        issue_bill(bill)

        first_payment = record_payment(
            bill=bill,
            amount="100.00",
            method=Payment.Method.CASH,
        )
        bill.refresh_from_db()
        self.assertEqual(first_payment.status, Payment.Status.PAID)
        self.assertEqual(bill.status, Bill.Status.PARTIALLY_PAID)
        self.assertEqual(bill.paid_amount, Decimal("100.00"))

        record_payment(
            bill=bill,
            amount="200.00",
            method=Payment.Method.TERMINAL,
        )
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.PAID)
        self.assertEqual(bill.paid_amount, Decimal("300.00"))
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, self.session.Status.ACTIVE)

    def test_record_payment_rejects_unknown_method(self):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )

        with self.assertRaisesMessage(BillingServiceError, "Неизвестный способ оплаты."):
            record_payment(
                bill=bill,
                amount="100.00",
                method="crypto",
            )

    def test_bill_repository_can_load_paid_bill_by_public_id(self):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )
        record_payment(
            bill=bill,
            amount=bill.total_amount,
            method=Payment.Method.CASH,
        )
        bill.refresh_from_db()

        loaded_bill = BillRepository.by_public_id_for_partner(
            self.partner.id,
            bill.public_id,
        )

        self.assertEqual(loaded_bill.id, bill.id)
        self.assertEqual(loaded_bill.status, Bill.Status.PAID)

    def test_billing_request_repository_can_load_processed_request_by_id(self):
        request = BillingRequest.objects.create(
            partner=self.partner,
            table=self.table,
            guest=self.session.guest,
            table_session=self.session,
            request_type=BillingRequest.RequestType.CUSTOM_SPLIT,
            status=BillingRequest.Status.PROCESSED,
        )

        loaded_request = BillingRequestRepository.by_id_for_partner(
            self.partner.id,
            request.id,
        )

        self.assertEqual(loaded_request.id, request.id)
        self.assertEqual(loaded_request.status, BillingRequest.Status.PROCESSED)

    def test_full_payment_marks_ready_orders_completed_and_closes_session(self):
        transition_order_status(
            order=self.order,
            to_status=Order.Status.ACCEPTED,
            actor_user=self.manager.user,
            note="Taken by manager",
        )
        transition_order_status(
            order=self.order,
            to_status=Order.Status.READY,
            actor_user=self.manager.user,
            note="Ready for payment",
        )
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )

        payment = record_payment(
            bill=bill,
            amount=bill.total_amount,
            method=Payment.Method.TERMINAL,
            created_by=self.manager.user,
        )

        self.order.refresh_from_db()
        bill.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(payment.method, Payment.Method.TERMINAL)
        self.assertEqual(self.order.status, Order.Status.COMPLETED)
        self.assertEqual(self.order.payment_method, Order.PaymentMethod.TERMINAL)
        self.assertIsNotNone(self.order.paid_at)
        self.assertEqual(bill.status, Bill.Status.PAID)
        self.assertEqual(self.session.status, self.session.Status.CLOSED)

    def test_full_payment_keeps_session_active_when_auto_close_setting_is_disabled(self):
        self.partner.bot_settings.auto_close_table_session_after_payment = False
        self.partner.bot_settings.save(
            update_fields=["auto_close_table_session_after_payment", "updated_at"]
        )
        transition_order_status(
            order=self.order,
            to_status=Order.Status.ACCEPTED,
            actor_user=self.manager.user,
            note="Taken by manager",
        )
        transition_order_status(
            order=self.order,
            to_status=Order.Status.READY,
            actor_user=self.manager.user,
            note="Ready for payment",
        )
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )

        record_payment(
            bill=bill,
            amount=bill.total_amount,
            method=Payment.Method.TERMINAL,
            created_by=self.manager.user,
        )

        self.order.refresh_from_db()
        bill.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.COMPLETED)
        self.assertEqual(bill.status, Bill.Status.PAID)
        self.assertEqual(self.session.status, self.session.Status.ACTIVE)

    def test_create_shared_bill_for_table_collects_all_unbilled_orders(self):
        second_session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=5550002,
            payload=self.table.deep_link_payload,
            username="billing_guest_two",
            first_name="Guest Two",
        )
        second_order = create_order_from_session(
            partner_id=self.partner.id,
            table_session=second_session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": 1,
                }
            ],
        )

        bill = create_shared_bill_for_table(
            partner_id=self.partner.id,
            table_id=self.table.id,
        )

        self.assertEqual(bill.kind, Bill.Kind.SHARED)
        self.assertEqual(bill.bill_orders.count(), 2)
        self.assertEqual(
            set(bill.bill_orders.values_list("order_id", flat=True)),
            {self.order.id, second_order.id},
        )
        self.assertEqual(bill.total_amount, Decimal("450.00"))

    def test_shared_bill_payment_updates_multiple_ready_orders(self):
        second_order = create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": 1,
                }
            ],
        )
        for order in (self.order, second_order):
            transition_order_status(
                order=order,
                to_status=Order.Status.ACCEPTED,
                actor_user=self.manager.user,
                note="Taken by manager",
            )
            transition_order_status(
                order=order,
                to_status=Order.Status.READY,
                actor_user=self.manager.user,
                note="Ready for payment",
            )

        bill = create_shared_bill_for_table(
            partner_id=self.partner.id,
            table_id=self.table.id,
        )

        record_payment(
            bill=bill,
            amount=bill.total_amount,
            method=Payment.Method.TERMINAL,
            created_by=self.manager.user,
        )

        self.order.refresh_from_db()
        second_order.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.COMPLETED)
        self.assertEqual(second_order.status, Order.Status.COMPLETED)
        self.assertEqual(self.order.payment_method, Order.PaymentMethod.TERMINAL)
        self.assertEqual(second_order.payment_method, Order.PaymentMethod.TERMINAL)
        self.assertEqual(self.session.status, self.session.Status.CLOSED)

    def test_create_personal_bills_for_table_groups_orders_by_guest(self):
        create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": 1,
                }
            ],
        )
        second_session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=5550003,
            payload=self.table.deep_link_payload,
            username="billing_guest_three",
            first_name="Guest Three",
        )
        create_order_from_session(
            partner_id=self.partner.id,
            table_session=second_session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": 2,
                }
            ],
        )

        bills = create_personal_bills_for_table(
            partner_id=self.partner.id,
            table_id=self.table.id,
        )

        self.assertEqual(len(bills), 2)
        self.assertEqual({bill.kind for bill in bills}, {Bill.Kind.PERSONAL})
        totals = sorted(bill.total_amount for bill in bills)
        self.assertEqual(totals, [Decimal("300.00"), Decimal("450.00")])

    def test_create_billing_request_for_personal_bill_prepares_bill_and_notifies_staff(self):
        with patch("apps.notifications.services._send_telegram_messages", new=_successful_send):
            result = create_billing_request_for_telegram_user(
                partner_id=self.partner.id,
                telegram_id=5550001,
                request_type=BillingRequest.RequestType.PERSONAL,
            )

        self.assertEqual(result.request.status, BillingRequest.Status.AUTO_PREPARED)
        self.assertIsNotNone(result.bill)
        self.assertEqual(result.bill.kind, Bill.Kind.PERSONAL)
        self.assertEqual(result.notified_count, 1)
        self.assertTrue(
            StaffNotification.objects.filter(
                partner=self.partner,
                category=StaffNotification.Category.BILLING_REQUEST,
            ).exists()
        )

    def test_create_billing_request_for_custom_split_creates_open_request(self):
        with patch("apps.notifications.services._send_telegram_messages", new=_successful_send):
            result = create_billing_request_for_telegram_user(
                partner_id=self.partner.id,
                telegram_id=5550001,
                request_type=BillingRequest.RequestType.CUSTOM_SPLIT,
            )

        self.assertEqual(result.request.status, BillingRequest.Status.OPEN)
        self.assertIsNone(result.bill)

    def test_paid_bill_marks_related_billing_request_processed(self):
        with patch("apps.notifications.services._send_telegram_messages", new=_successful_send):
            result = create_billing_request_for_telegram_user(
                partner_id=self.partner.id,
                telegram_id=5550001,
                request_type=BillingRequest.RequestType.PERSONAL,
            )

        record_payment(
            bill=result.bill,
            amount=result.bill.total_amount,
            method=Payment.Method.CASH,
        )
        result.request.refresh_from_db()
        self.assertEqual(result.request.status, BillingRequest.Status.PROCESSED)
        self.assertIsNotNone(result.request.processed_at)

    def test_partial_payment_keeps_table_session_active(self):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )

        record_payment(
            bill=bill,
            amount="100.00",
            method=Payment.Method.CASH,
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, self.session.Status.ACTIVE)

    def test_paying_one_bill_does_not_close_table_when_another_order_is_unsettled(self):
        second_order = create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": 1,
                }
            ],
        )
        transition_order_status(
            order=self.order,
            to_status=Order.Status.ACCEPTED,
            actor_user=self.manager.user,
            note="Taken by manager",
        )
        transition_order_status(
            order=self.order,
            to_status=Order.Status.READY,
            actor_user=self.manager.user,
            note="Ready for payment",
        )
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
        )

        record_payment(
            bill=bill,
            amount=bill.total_amount,
            method=Payment.Method.CASH,
        )

        self.session.refresh_from_db()
        second_order.refresh_from_db()
        self.assertEqual(self.session.status, self.session.Status.ACTIVE)
        self.assertEqual(second_order.status, Order.Status.NEW)

    def test_mark_billing_request_processed_updates_status(self):
        request = BillingRequest.objects.create(
            partner=self.partner,
            table=self.table,
            guest=self.session.guest,
            table_session=self.session,
            request_type=BillingRequest.RequestType.CUSTOM_SPLIT,
            status=BillingRequest.Status.OPEN,
        )

        updated_request = mark_billing_request_processed(request)

        self.assertEqual(updated_request.status, BillingRequest.Status.PROCESSED)
        self.assertIsNotNone(updated_request.processed_at)

    def test_cancel_billing_request_updates_status(self):
        request = BillingRequest.objects.create(
            partner=self.partner,
            table=self.table,
            guest=self.session.guest,
            table_session=self.session,
            request_type=BillingRequest.RequestType.CUSTOM_SPLIT,
            status=BillingRequest.Status.OPEN,
        )

        updated_request = cancel_billing_request(request)

        self.assertEqual(updated_request.status, BillingRequest.Status.CANCELED)
        self.assertIsNotNone(updated_request.processed_at)

    def test_redeem_bonus_for_bill_reduces_total_and_guest_balance(self):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
            kind=Bill.Kind.PERSONAL,
        )

        redeemed_amount = redeem_bonus_for_bill(
            bill=bill,
            created_by=self.manager.user,
        )

        bill.refresh_from_db()
        self.session.guest.refresh_from_db()
        self.assertEqual(redeemed_amount, Decimal("90.00"))
        self.assertEqual(bill.bonus_spent_amount, Decimal("90.00"))
        self.assertEqual(bill.total_amount, Decimal("210.00"))
        self.assertEqual(self.session.guest.loyalty_balance, Decimal("160.00"))
        self.assertTrue(
            BonusTransaction.objects.filter(
                partner=self.partner,
                guest=self.session.guest,
                transaction_type=BonusTransaction.TransactionType.REDEMPTION,
                amount=Decimal("90.00"),
            ).exists()
        )

    def test_redeem_bonus_for_bill_requires_no_payments_yet(self):
        bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(self.order.id)],
            kind=Bill.Kind.PERSONAL,
        )
        record_payment(
            bill=bill,
            amount="50.00",
            method=Payment.Method.CASH,
        )

        with self.assertRaisesMessage(
            BillingServiceError,
            "Списание бонусов нужно делать до первой оплаты по счёту.",
        ):
            redeem_bonus_for_bill(
                bill=bill,
                created_by=self.manager.user,
            )
