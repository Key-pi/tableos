from django.test import TestCase

from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Order
from apps.orders.repositories import OrderRepository
from apps.orders.selectors import get_open_orders_for_partner
from apps.orders.services import (
    add_items_to_cart_for_telegram_user,
    confirm_order_received_by_guest,
    get_available_staff_actions,
    transition_order_status,
)
from apps.partners.models import Partner
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import GuestProfile, TelegramAccount, User


class OrderItemCodeParsingTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Smoke QA",
            slug="smoke-qa",
            status=Partner.Status.ACTIVE,
        )
        self.table = Table.objects.create(
            partner=self.partner,
            number=1,
            name="Table 1",
            qr_token="smokeqa01",
        )
        category = MenuCategory.objects.create(
            partner=self.partner,
            name="Напитки",
            sort_order=10,
        )
        self.menu_item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="SMKDR001",
            name="Lemonade",
            price="150.00",
            sort_order=10,
        )
        activate_table_session(
            partner_id=self.partner.id,
            telegram_id=777001,
            payload=self.table.deep_link_payload,
            username="qa_guest",
            first_name="QA",
        )

    def test_add_to_cart_accepts_uppercase_seed_public_id(self):
        cart = add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id,
            telegram_id=777001,
            raw_items="SMKDR001x2",
        )

        self.assertEqual(cart.items.count(), 1)
        self.assertEqual(cart.items.first().menu_item_id, self.menu_item.id)
        self.assertEqual(cart.items.first().quantity, 2)

    def test_add_to_cart_accepts_lowercase_variant_of_public_id(self):
        cart = add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id,
            telegram_id=777001,
            raw_items="smkdr001x1",
        )

        self.assertEqual(cart.items.count(), 1)
        self.assertEqual(cart.items.first().menu_item_id, self.menu_item.id)


class OrderRepositorySelectRelatedTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Repository QA",
            slug="repository-qa",
            status=Partner.Status.ACTIVE,
        )
        self.telegram_account = TelegramAccount.objects.create(
            telegram_id=770001,
            username="repo_guest",
        )
        self.guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=self.telegram_account,
        )
        self.table = Table.objects.create(
            partner=self.partner,
            number=5,
            name="Table 5",
            qr_token="repoqa05",
        )
        self.table_session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            payload=self.table.deep_link_payload,
            username=self.telegram_account.username,
            first_name="Repo",
        )
        self.order = Order.objects.create(
            partner=self.partner,
            guest=self.guest,
            table=self.table,
            table_session=self.table_session,
            subtotal_amount="150.00",
            total_amount="150.00",
        )

    def test_by_public_id_prefetches_guest_telegram_account_for_async_handlers(self):
        order = OrderRepository.by_public_id_for_partner(
            self.partner.id,
            self.order.public_id,
        )

        self.assertIn("telegram_account", order.guest._state.fields_cache)


class OrderOwnershipTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Ownership Venue",
            slug="ownership-venue",
            status=Partner.Status.ACTIVE,
        )
        telegram_account = TelegramAccount.objects.create(
            telegram_id=880001,
            username="owner_guest",
        )
        self.table = Table.objects.create(
            partner=self.partner,
            number=7,
            name="Table 7",
            qr_token="ownerqa07",
        )
        session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=telegram_account.telegram_id,
            payload=self.table.deep_link_payload,
            username=telegram_account.username,
            first_name="Owner",
        )
        self.order = Order.objects.create(
            partner=self.partner,
            guest=session.guest,
            table=self.table,
            table_session=session,
            subtotal_amount="180.00",
            total_amount="180.00",
        )
        self.user = User.objects.create_user(
            username="floor_manager",
            password="tableos12345",
            partner=self.partner,
            role=User.Role.MANAGER,
        )
        self.employee = EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.user,
        )

    def test_accepting_order_assigns_responsible_employee(self):
        transition_order_status(
            order=self.order,
            to_status=Order.Status.ACCEPTED,
            actor_user=self.user,
            note="Taken by manager",
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.assigned_employee_id, self.employee.id)
        self.assertIsNotNone(self.order.accepted_at)

    def test_accepted_order_can_be_marked_ready_in_operational_flow(self):
        transition_order_status(
            order=self.order,
            to_status=Order.Status.ACCEPTED,
            actor_user=self.user,
            note="Taken by manager",
        )

        transition_order_status(
            order=self.order,
            to_status=Order.Status.READY,
            actor_user=self.user,
            note="Ready in one tap",
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.READY)

    def test_staff_actions_hide_internal_preparing_step(self):
        self.assertEqual(
            get_available_staff_actions(Order.Status.ACCEPTED),
            [Order.Status.READY, Order.Status.CANCELED],
        )
        self.assertEqual(
            get_available_staff_actions(Order.Status.READY),
            [Order.Status.COMPLETED, Order.Status.CANCELED],
        )


class OrderFinalityVisibilityTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Finality Venue",
            slug="finality-venue",
            status=Partner.Status.ACTIVE,
        )
        self.telegram_account = TelegramAccount.objects.create(
            telegram_id=990001,
            username="final_guest",
        )
        self.table = Table.objects.create(
            partner=self.partner,
            number=9,
            name="Table 9",
            qr_token="finality09",
        )
        self.session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            payload=self.table.deep_link_payload,
            username=self.telegram_account.username,
            first_name="Final",
        )
        self.order = Order.objects.create(
            partner=self.partner,
            guest=self.session.guest,
            table=self.table,
            table_session=self.session,
            subtotal_amount="210.00",
            total_amount="210.00",
        )

    def test_open_orders_selector_includes_completed_unpaid_orders(self):
        self.order.status = Order.Status.COMPLETED
        self.order.received_at = self.order.updated_at
        self.order.save(update_fields=["status", "received_at", "updated_at"])

        orders = list(get_open_orders_for_partner(self.partner.id))

        self.assertEqual([order.id for order in orders], [self.order.id])

    def test_open_orders_selector_excludes_completed_paid_orders(self):
        self.order.status = Order.Status.COMPLETED
        self.order.received_at = self.order.updated_at
        self.order.paid_at = self.order.updated_at
        self.order.save(update_fields=["status", "received_at", "paid_at", "updated_at"])

        orders = list(get_open_orders_for_partner(self.partner.id))

        self.assertEqual(orders, [])

    def test_guest_can_confirm_received_before_payment(self):
        self.order.status = Order.Status.ACCEPTED
        self.order.save(update_fields=["status", "updated_at"])

        order = confirm_order_received_by_guest(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            order_public_id=self.order.public_id,
        )

        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertIsNotNone(order.received_at)
        self.assertIsNone(order.paid_at)

    def test_order_repository_open_for_partner_includes_completed_unpaid_order(self):
        self.order.status = Order.Status.COMPLETED
        self.order.received_at = self.order.updated_at
        self.order.save(update_fields=["status", "received_at", "updated_at"])

        orders = list(OrderRepository.open_for_partner(self.partner.id))

        self.assertEqual([order.id for order in orders], [self.order.id])
