from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Cart, CartItem, Order
from apps.orders.repositories import OrderRepository
from apps.orders.selectors import get_open_orders_for_partner
from apps.orders.services import (
    add_items_to_cart_for_telegram_user,
    checkout_active_cart_for_telegram_user,
    confirm_order_received_by_guest,
    create_order,
    get_active_cart_for_telegram_user,
    get_available_staff_actions,
    transition_order_status,
    OrderFlowError,
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

    def test_add_to_cart_rejects_item_from_inactive_category(self):
        self.menu_item.category.is_active = False
        self.menu_item.category.save(update_fields=["is_active", "updated_at"])

        with self.assertRaisesMessage(
            OrderFlowError,
            "Некоторые позиции недоступны или не принадлежат этому заведению: SMKDR001.",
        ):
            add_items_to_cart_for_telegram_user(
                partner_id=self.partner.id,
                telegram_id=777001,
                raw_items="SMKDR001x1",
            )


class GenericOrderCreationTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Generic Orders Venue",
            slug="generic-orders-venue",
            status=Partner.Status.ACTIVE,
        )
        self.telegram_account = TelegramAccount.objects.create(
            telegram_id=771001,
            username="generic_guest",
        )
        self.guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=self.telegram_account,
        )
        self.table = Table.objects.create(
            partner=self.partner,
            number=3,
            name="Table 3",
            qr_token="generic03",
        )
        self.table_session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            payload=self.table.deep_link_payload,
            username=self.telegram_account.username,
            first_name="Generic",
        )
        category = MenuCategory.objects.create(
            partner=self.partner,
            name="Food",
            sort_order=10,
        )
        self.menu_item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="GEN001",
            name="Burger",
            price="220.00",
            sort_order=10,
        )

    def test_create_order_rejects_empty_items(self):
        with self.assertRaisesMessage(OrderFlowError, "Нельзя создать заказ без позиций."):
            create_order(
                partner_id=self.partner.id,
                guest=self.guest,
                items=[],
            )

    def test_create_order_allows_preparation_for_non_table_flow(self):
        order = create_order(
            partner_id=self.partner.id,
            guest=self.guest,
            table=None,
            table_session=None,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": self.menu_item.price,
                    "quantity": 2,
                }
            ],
            comment="Off-table prepared order",
        )

        self.assertIsNone(order.table_id)
        self.assertIsNone(order.table_session_id)
        self.assertEqual(order.total_amount, Decimal("440.00"))
        self.assertEqual(order.items.count(), 1)


class ActiveCartConstraintTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Cart Constraint Venue",
            slug="cart-constraint-venue",
            status=Partner.Status.ACTIVE,
        )
        self.telegram_account = TelegramAccount.objects.create(
            telegram_id=772001,
            username="cart_guest",
        )
        self.guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=self.telegram_account,
        )
        self.table = Table.objects.create(
            partner=self.partner,
            number=6,
            name="Table 6",
            qr_token="cartconstraint06",
        )
        self.table_session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            payload=self.table.deep_link_payload,
            username=self.telegram_account.username,
            first_name="Cart",
        )
        category = MenuCategory.objects.create(
            partner=self.partner,
            name="Desserts",
            sort_order=10,
        )
        self.menu_item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="CRT001",
            name="Cake",
            price="90.00",
            sort_order=10,
        )

    def test_model_enforces_single_active_cart_per_guest(self):
        Cart.objects.create(
            partner=self.partner,
            guest=self.guest,
            table_session=self.table_session,
            status=Cart.Status.ACTIVE,
        )

        with self.assertRaises(IntegrityError):
            Cart.objects.create(
                partner=self.partner,
                guest=self.guest,
                table_session=self.table_session,
                status=Cart.Status.ACTIVE,
            )

    def test_checked_out_cart_does_not_block_new_active_cart(self):
        Cart.objects.create(
            partner=self.partner,
            guest=self.guest,
            table_session=self.table_session,
            status=Cart.Status.CHECKED_OUT,
        )

        active_cart = Cart.objects.create(
            partner=self.partner,
            guest=self.guest,
            table_session=self.table_session,
            status=Cart.Status.ACTIVE,
        )

        self.assertEqual(active_cart.status, Cart.Status.ACTIVE)

    def test_active_cart_with_items_is_abandoned_when_guest_opens_new_table_session(self):
        category = MenuCategory.objects.create(
            partner=self.partner,
            name="Tea",
            sort_order=20,
        )
        menu_item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="CRT002",
            name="Green Tea",
            price="80.00",
            sort_order=20,
        )
        cart = Cart.objects.create(
            partner=self.partner,
            guest=self.guest,
            table_session=self.table_session,
            status=Cart.Status.ACTIVE,
        )
        CartItem.objects.create(
            partner=self.partner,
            cart=cart,
            menu_item=menu_item,
            item_name=menu_item.name,
            unit_price=menu_item.price,
            quantity=1,
        )
        second_table = Table.objects.create(
            partner=self.partner,
            number=7,
            name="Table 7",
            qr_token="cartconstraint07",
        )
        second_session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            payload=second_table.deep_link_payload,
            username=self.telegram_account.username,
            first_name="Cart",
        )

        active_cart = get_active_cart_for_telegram_user(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
        )

        cart.refresh_from_db()
        self.assertEqual(cart.status, Cart.Status.ABANDONED)
        self.assertIsNone(active_cart)

        recreated_cart = add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id,
            telegram_id=self.telegram_account.telegram_id,
            raw_items="CRT002x1",
        )

        self.assertEqual(recreated_cart.table_session_id, second_session.id)
        self.assertEqual(recreated_cart.status, Cart.Status.ACTIVE)

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
