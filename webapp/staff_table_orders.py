"""An order to a table from the staff tablet (PQ-052, step 2).

The screen taps a table on the plan and picks items as in the quick sale;
the order is made by ``apps/orders/services.py:create_staff_table_order``
(rights, the module, the guest's rules, the table and visit, the key).
"""

from __future__ import annotations

import re
import uuid

from apps.menu.catalog import local_now, not_on_sale_message
from apps.menu.repositories import MenuItemRepository
from apps.orders.models import Order
from apps.orders.services import (
    OrderFlowError,
    create_staff_table_order,
    guest_for_staff_order,
    resolve_staff_order_lines,
)
from bot.services.staff_rights import can_take_table_orders
from core.i18n import N_, _
from webapp.context import WebAppDenied
from webapp.staff_context import StaffRequest
from webapp.staff_sales import _items, menu_json
from webapp.staff_views import staff_api
from webapp.views import _money, _text

# The screen makes one key per order and sends it again on a retry.
ORDER_KEY = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _right(staff: StaffRequest) -> None:
    if not can_take_table_orders(staff.employee, staff.content):
        raise WebAppDenied(
            403, N_("Заказ на стол делают официант, кассир, менеджер или владелец."),
            code="no_right",
        )


def _uuid(value, message) -> uuid.UUID:
    try:
        return uuid.UUID(hex=str(value))
    except ValueError as exc:
        raise WebAppDenied(400, message) from exc


@staff_api("GET")
def menu_view(request, staff: StaffRequest, body: dict) -> dict:
    """What a guest could order at a table now."""

    _right(staff)
    now = local_now(staff.partner)
    # Only what can be ordered this minute: a category out of its hours
    # (catalog, PQ-049) is not offered — the server would refuse it anyway.
    items = [
        item for item in MenuItemRepository.active_for_partner(staff.partner.id)
        .select_related("partner", "category__parent")
        if item.is_sold_in(Order.Channel.TABLE) and not_on_sale_message(item, now) is None
    ]
    return menu_json(staff, items)


def _lines(staff: StaffRequest, body: dict) -> list[dict]:
    try:
        return resolve_staff_order_lines(partner_id=staff.partner.id, items=_items(body))
    except OrderFlowError as exc:
        raise WebAppDenied(400, str(exc)) from exc


@staff_api("POST")
def preview_view(request, staff: StaffRequest, body: dict) -> dict:
    """The order so far: its lines, the total and the guest named by code."""

    _right(staff)
    lines = _lines(staff, body) if body.get("items") else []
    try:
        guest = guest_for_staff_order(staff.partner.id, _text(body, "customer_code"))
    except OrderFlowError as exc:
        raise WebAppDenied(400, str(exc)) from exc
    return {"order": {
        "guest": guest.staff_label() if guest is not None else "",
        "items": [
            {"name": line["item_name"], "quantity": line["quantity"],
             "total": _money(line["unit_price"] * line["quantity"])}
            for line in lines
        ],
        "total": _money(sum(line["unit_price"] * line["quantity"] for line in lines)),
    }}


@staff_api("POST")
def order_view(request, staff: StaffRequest, body: dict) -> dict:
    """``{"table", "visit"?, "customer_code"?, "items", "comment"?, "key"}``."""

    _right(staff)
    key = _text(body, "key")
    if key and not ORDER_KEY.fullmatch(key):
        raise WebAppDenied(400, N_("Обновите экран и отправьте заказ ещё раз."))
    table_id = _uuid(_text(body, "table"), N_("Стол не найден."))
    visit = _text(body, "visit")
    visit_id = _uuid(visit, N_("Компания за столом не найдена.")) if visit else None
    repeated = bool(key) and Order.objects.filter(
        partner_id=staff.partner.id, request_key=key
    ).exists()
    try:
        order = create_staff_table_order(
            partner_id=staff.partner.id, actor=staff.employee, table_id=table_id,
            visit_id=visit_id, customer_code=_text(body, "customer_code"), items=_items(body),
            comment=_text(body, "comment"), request_key=key,
        )
    except OrderFlowError as exc:
        raise WebAppDenied(400, str(exc)) from exc
    text = (
        N_("Заказ #{number} на стол {table} уже отправлен.") if repeated
        else N_("Заказ #{number} на стол {table} отправлен на кухню и бар.")
    )
    return {
        "ok": True, "order": order.public_id, "repeated": repeated,
        "notice": _(text, number=order.public_id, table=order.table.number),
    }
