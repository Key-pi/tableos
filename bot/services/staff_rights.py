"""Which staff sections a person may use (PQ-044, PQ-048): the role and the
venue's modules. The bot's work mode and the Mini App's staff screen ask the
same; the services check money rights again (``user_can_adjust_bills``,
``confirm_transfer``)."""

from __future__ import annotations

STAFF_OPERATIONS_ROLES = frozenset({"owner", "manager", "cashier"})


def can_manage_billing(employee, content) -> bool:
    return employee.user.role in STAFF_OPERATIONS_ROLES and content.supports_billing()


def can_view_tables(employee, content) -> bool:
    return employee.user.role in STAFF_OPERATIONS_ROLES and content.supports_tables()


def can_take_table_orders(employee, content) -> bool:
    """A waiter, a cashier, a manager or the owner orders to a table from the
    tablet (PQ-052, step 2); the service checks again."""

    from apps.orders.services import TABLE_ORDER_ROLES

    return employee.user.role in TABLE_ORDER_ROLES and content.supports_tablet_orders()


def can_quick_sale(employee, content) -> bool:
    return employee.user.role in STAFF_OPERATIONS_ROLES and content.supports_quick_sale()


def can_view_day_report(employee, content) -> bool:
    return employee.user.role in STAFF_OPERATIONS_ROLES and content.supports_reports()
