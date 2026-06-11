from decimal import Decimal

from django.test import TestCase

from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Order
from apps.orders.services import create_order_from_session, transition_order_status
from apps.partners.models import Partner
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import TelegramAccount, User


class StaffNotificationPreferenceTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Notify Venue",
            slug="notify-venue",
            status=Partner.Status.ACTIVE,
        )
        self.table = Table.objects.create(
            partner=self.partner,
            number=1,
            name="Table 1",
            qr_token="notify_table_01",
        )
        category = MenuCategory.objects.create(
            partner=self.partner,
            name="Drinks",
            sort_order=10,
        )
        self.menu_item = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            public_id="NOTI0001",
            name="Lemonade",
            price="150.00",
            sort_order=10,
        )
        self.session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=930001,
            payload=self.table.deep_link_payload,
            username="notify_guest",
            first_name="Notify",
        )

        self.manager_user = User.objects.create_user(
            username="notify_manager",
            password="pass12345",
            partner=self.partner,
            role=User.Role.MANAGER,
        )
        manager_account = TelegramAccount.objects.create(
            telegram_id=930101,
            username="notify_manager",
        )
        self.manager_profile = EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.manager_user,
            telegram_account=manager_account,
            title="Manager",
            bot_notifications_enabled=True,
            notify_on_order_created=True,
            notify_on_order_status_changed=True,
        )

        self.waiter_user = User.objects.create_user(
            username="silent_waiter",
            password="pass12345",
            partner=self.partner,
            role=User.Role.WAITER,
        )
        waiter_account = TelegramAccount.objects.create(
            telegram_id=930102,
            username="silent_waiter",
        )
        self.waiter_profile = EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.waiter_user,
            telegram_account=waiter_account,
            title="Waiter",
            bot_notifications_enabled=False,
            notify_on_order_created=False,
            notify_on_order_status_changed=False,
        )

    def test_order_created_notifications_follow_employee_preferences(self):
        order = create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": Decimal("150.00"),
                    "quantity": 2,
                }
            ],
        )

        self.assertEqual(order.staff_notifications.count(), 1)
        self.assertEqual(order.staff_notifications.first().employee_id, self.manager_profile.id)

    def test_status_change_notifications_follow_employee_preferences(self):
        order = create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": self.menu_item.name,
                    "unit_price": Decimal("150.00"),
                    "quantity": 2,
                }
            ],
        )

        transition_order_status(order=order, to_status=Order.Status.ACCEPTED)

        status_notifications = order.staff_notifications.filter(
            category="order_status_changed"
        )
        self.assertEqual(status_notifications.count(), 1)
        self.assertEqual(status_notifications.first().employee_id, self.manager_profile.id)
