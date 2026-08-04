from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase

from apps.employees.models import EmployeeProfile
from apps.orders.services import OrderFlowError
from apps.partners.models import Partner
from apps.users.models import TelegramAccount, User
from bot.handlers.staff import (
    _can_manage_billing,
    _can_use_quick_sale,
    _can_view_day_report,
    _can_view_open_orders,
    _can_view_tables,
    _ensure_open_orders_allowed,
    _resolve_staff_employee_by_telegram_id,
)
from bot.services.content import BotContent


class _StubUser:
    def __init__(self, role: str) -> None:
        self.role = role


class _StubEmployee:
    """Minimal stand-in: staff helpers only read ``user.role`` and ``bot_content``."""

    def __init__(self, role: str, content: BotContent | None) -> None:
        self.user = _StubUser(role)
        self.bot_content = content


class StaffModuleGatingTests(SimpleTestCase):
    def test_cashier_with_all_modules_enabled_can_use_everything(self):
        employee = _StubEmployee("cashier", BotContent.defaults())

        self.assertTrue(_can_use_quick_sale(employee))
        self.assertTrue(_can_view_day_report(employee))
        # Billing module is off in defaults, so payments stay hidden for staff.
        self.assertFalse(_can_manage_billing(employee))

    def test_disabled_modules_hide_staff_capabilities(self):
        content = BotContent.defaults()
        content.module_quick_sale_enabled = False
        content.module_reports_enabled = False
        employee = _StubEmployee("owner", content)

        self.assertFalse(_can_use_quick_sale(employee))
        self.assertFalse(_can_view_day_report(employee))

    def test_enabled_billing_module_exposes_payments(self):
        content = BotContent.defaults()
        content.module_billing_enabled = True
        employee = _StubEmployee("manager", content)

        self.assertTrue(_can_manage_billing(employee))

    def test_role_still_required_even_with_modules_enabled(self):
        employee = _StubEmployee("waiter", BotContent.defaults())

        self.assertFalse(_can_use_quick_sale(employee))
        self.assertFalse(_can_view_day_report(employee))
        self.assertFalse(_can_manage_billing(employee))

    def test_open_orders_visible_to_any_role_when_orders_module_on(self):
        waiter = _StubEmployee("waiter", BotContent.defaults())
        self.assertTrue(_can_view_open_orders(waiter))

        content = BotContent.defaults()
        content.module_orders_enabled = False
        self.assertFalse(_can_view_open_orders(_StubEmployee("owner", content)))

    def test_stale_open_order_action_is_denied_when_module_is_disabled(self):
        content = BotContent.defaults()
        content.module_orders_enabled = False

        with self.assertRaisesRegex(OrderFlowError, "Модуль заказов отключён"):
            _ensure_open_orders_allowed(_StubEmployee("owner", content))

    def test_tables_button_needs_operations_role_and_tables_module(self):
        # Tables feed is gated by the tables module + an operations role, and no
        # longer requires the billing module (defaults: tables on, billing off).
        content = BotContent.defaults()
        self.assertFalse(content.module_billing_enabled)
        self.assertTrue(_can_view_tables(_StubEmployee("manager", content)))

        # Waiters never reach the table feed.
        self.assertFalse(_can_view_tables(_StubEmployee("waiter", content)))

        # Tables module off hides the feed even for managers.
        no_tables = BotContent.defaults()
        no_tables.module_tables_enabled = False
        self.assertFalse(_can_view_tables(_StubEmployee("manager", no_tables)))


class StaffActorResolutionTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Staff Adapter Venue",
            slug="staff-adapter-venue",
            status=Partner.Status.ACTIVE,
        )
        self.user = User.objects.create_user(
            username="staff_adapter_user",
            password="pass12345",
            partner=self.partner,
            role=User.Role.WAITER,
        )
        self.telegram_account = TelegramAccount.objects.create(
            telegram_id=8900001,
            username="staff_adapter_user",
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.user,
            telegram_account=self.telegram_account,
            is_active=True,
        )

    @patch("bot.handlers.staff.resolve_partner_for_bot_token")
    def test_staff_adapter_rejects_disabled_django_user(self, resolve_partner):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        resolve_partner.return_value = self.partner

        with self.assertRaisesRegex(OrderFlowError, "Сотрудник не привязан"):
            async_to_sync(_resolve_staff_employee_by_telegram_id)(
                self.telegram_account.telegram_id,
                SimpleNamespace(token="staff-adapter-token"),
            )
