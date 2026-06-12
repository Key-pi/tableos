from django.test import SimpleTestCase

from bot.handlers.staff import (
    _can_manage_billing,
    _can_use_quick_sale,
    _can_view_day_report,
    _can_view_open_orders,
    _can_view_tables,
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

    def test_tables_button_needs_billing_role_and_tables_module(self):
        content = BotContent.defaults()
        content.module_billing_enabled = True  # billing capability for the feed
        manager = _StubEmployee("manager", content)
        self.assertTrue(_can_view_tables(manager))

        # Waiters never reach the table billing feed.
        self.assertFalse(_can_view_tables(_StubEmployee("waiter", content)))

        # Tables module off hides the feed even for managers.
        no_tables = BotContent.defaults()
        no_tables.module_billing_enabled = True
        no_tables.module_tables_enabled = False
        self.assertFalse(_can_view_tables(_StubEmployee("manager", no_tables)))
