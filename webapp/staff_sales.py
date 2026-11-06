"""Quick sale and the day report on the Mini App's staff screen (PQ-048).

As the bot's work mode: the same rights (``bot/services/staff_rights.py``),
the same services (``preview_walk_in_sale_by_customer_code``,
``register_walk_in_sale_from_menu_items_by_customer_code``,
``build_daily_operations_report``). The screen keeps the chosen items itself
and sends them whole each time; nothing is saved until «Провести».
"""

from __future__ import annotations

import re
import uuid

from apps.analytics.services import build_daily_operations_report
from apps.bonuses.services import (
    preview_walk_in_sale_by_customer_code,
    register_walk_in_sale_from_menu_items_by_customer_code,
    walk_in_sale_by_request_key,
)
from apps.menu.repositories import MenuItemRepository
from bot.services.staff_rights import can_quick_sale, can_view_day_report
from core.i18n import N_, _
from webapp.context import WebAppDenied
from webapp.staff_context import StaffRequest
from webapp.staff_views import staff_api
from webapp.views import _money, _text

MAX_LINES = 50


def _sales(staff: StaffRequest) -> None:
    if not can_quick_sale(staff.employee, staff.content):
        raise WebAppDenied(
            403, N_("Быстрая продажа сейчас доступна только владельцу, менеджеру или кассиру."),
            code="no_right",
        )


def _items(body: dict) -> list[dict]:
    raw = body.get("items")
    if not isinstance(raw, dict) or len(raw) > MAX_LINES:
        raise WebAppDenied(400, N_("Сначала добавьте позиции."))
    items = []
    for item_id, quantity in raw.items():
        try:
            menu_item_id = uuid.UUID(hex=str(item_id))
        except ValueError as exc:
            raise WebAppDenied(400, N_("Сначала добавьте позиции.")) from exc
        if not isinstance(quantity, int) or isinstance(quantity, bool) or not 0 < quantity <= 99:
            raise WebAppDenied(400, N_("Количество должно изменяться хотя бы на 1."))
        items.append({"menu_item_id": menu_item_id, "quantity": quantity})
    return items


def _redeem(body: dict) -> bool:
    return body.get("redeem") is True


@staff_api("GET")
def sale_menu_view(request, staff: StaffRequest, body: dict) -> dict:
    _sales(staff)
    categories: dict = {}
    for item in MenuItemRepository.active_for_partner(staff.partner.id):
        category = categories.setdefault(
            item.category_id, {"id": item.category_id.hex, "name": item.category.name, "items": []}
        )
        category["items"].append({
            "id": item.id.hex, "name": item.name, "price": _money(item.price),
            "photo": item.photo_url,
        })
    return {"categories": list(categories.values())}


def _preview_json(preview, redeem: bool) -> dict:
    spend = preview.redeemable_bonus_amount if redeem else 0
    return {
        "guest": preview.guest.staff_label() if preview.guest is not None else "",
        "items": [
            {
                "id": item["menu_item_id"].hex if hasattr(item["menu_item_id"], "hex")
                else str(item["menu_item_id"]).replace("-", ""),
                "name": item["item_name"], "quantity": item["quantity"],
                "total": _money(item["unit_price"] * item["quantity"]),
            }
            for item in preview.items
        ],
        "total": _money(preview.total_amount),
        "redeemable": _money(preview.redeemable_bonus_amount),
        "to_pay": _money(preview.total_amount - spend),
        "bonus_awarded": _money(preview.projected_bonus_amount),
    }


@staff_api("POST")
def sale_preview_view(request, staff: StaffRequest, body: dict) -> dict:
    """The current sale: who (a guest code or nobody), what, how much."""

    _sales(staff)
    redeem = _redeem(body)
    preview = preview_walk_in_sale_by_customer_code(
        partner_id=staff.partner.id,
        customer_code=_text(body, "customer_code"),
        items=_items(body) if body.get("items") else [],
        redeem_bonus=redeem,
    )
    return {"sale": _preview_json(preview, redeem)}


# The screen makes one key per sale (staff.js) and sends it again on a retry.
SALE_KEY = re.compile(r"[A-Za-z0-9_-]{8,64}")


@staff_api("POST")
def sale_confirm_view(request, staff: StaffRequest, body: dict) -> dict:
    _sales(staff)
    items = _items(body)
    if not items:
        raise WebAppDenied(400, N_("Сначала добавьте позиции."))
    key = _text(body, "key")
    if key and not SALE_KEY.fullmatch(key):
        raise WebAppDenied(400, N_("Обновите экран и проведите продажу ещё раз."))
    repeated = walk_in_sale_by_request_key(staff.partner.id, key) is not None
    sale = register_walk_in_sale_from_menu_items_by_customer_code(
        partner_id=staff.partner.id,
        customer_code=_text(body, "customer_code"),
        items=items,
        comment=_text(body, "comment"),
        created_by=staff.employee.user,
        redeem_bonus=_redeem(body),
        request_key=key,
    )
    text = (
        N_("Эта продажа уже сохранена: {amount} грн, бонусов начислено {bonus}.")
        if repeated
        else N_("Продажа сохранена: {amount} грн, бонусов начислено {bonus}.")
    )
    return {
        "ok": True,
        "repeated": repeated,
        "notice": _(text, amount=sale.amount, bonus=sale.bonus_awarded_amount),
    }


@staff_api("GET")
def report_view(request, staff: StaffRequest, body: dict) -> dict:
    if not can_view_day_report(staff.employee, staff.content):
        raise WebAppDenied(
            403, N_("Отчёт дня сейчас доступен только владельцу, менеджеру или кассиру."),
            code="no_right",
        )
    report = build_daily_operations_report(partner_id=staff.partner.id)
    return {
        "date": report.local_date_label,
        "money": [
            {"label": _("Всего выручка"), "value": _money(report.total_revenue), "strong": True},
            {"label": _("По счетам"), "value": _money(report.bill_revenue)},
            {"label": _("Быстрые продажи"), "value": _money(report.walk_in_revenue)},
            *([{"label": _("Возвраты"), "value": "-" + _money(report.refunds_total)}]
              if report.refunds_total else []),
        ],
        "counts": [
            {"label": _("Оплаченных счетов"), "value": report.paid_bills_count},
            {"label": _("Быстрых продаж"), "value": report.walk_in_sales_count},
            {"label": _("Создано заказов"), "value": report.created_orders_count},
            {"label": _("Открытых заказов"), "value": report.open_orders_count},
            {"label": _("Открытых счетов"), "value": report.open_bills_count},
        ],
        "channels": [
            {"label": item["label"], "orders": item["orders_count"],
             "revenue": _money(item["revenue"])}
            for item in report.channel_breakdown
        ],
        "methods": [
            {"label": str(method), "amount": _money(amount),
             "count": report.payment_method_counts.get(method, 0)}
            for method, amount in report.payment_method_totals.items()
            if amount > 0 or report.payment_method_counts.get(method, 0)
        ],
        "staff": [
            {"label": item["label"], "amount": _money(item["total_amount"]),
             "payments": item["payments_count"], "sales": item["walk_in_sales_count"]}
            for item in report.staff_breakdown[:12]
        ],
    }


# --- inviting a staff member (PQ-046) ---------------------------------------------


@staff_api("GET")
def invite_roles_view(request, staff: StaffRequest, body: dict) -> dict:
    from apps.employees.invites import roles_invitable_by
    from apps.users.models import User

    roles = roles_invitable_by(staff.employee.user, staff.partner.id)
    if not roles:
        raise WebAppDenied(
            403, N_("Приглашать сотрудников может владелец или менеджер."), code="no_right"
        )
    return {"roles": [{"code": role, "label": str(User.Role(role).label)} for role in roles]}


@staff_api("POST")
def invite_view(request, staff: StaffRequest, body: dict) -> dict:
    """A one-time link for 24 hours, as from the bot; the issuer confirms the
    person in the bot when they press it (``apps/employees/invites.py``)."""

    from urllib.parse import quote

    from apps.employees.invites import StaffInviteError, create_staff_invite, invite_link

    try:
        invite = create_staff_invite(
            partner_id=staff.partner.id, actor=staff.employee.user, role=_text(body, "role")
        )
    except StaffInviteError as exc:
        raise WebAppDenied(400, str(exc)) from exc
    link = invite_link(invite.code, staff.bot.username)
    share_text = _("Приглашение в команду «{partner_name}»", partner_name=staff.partner.name)
    return {
        "link": link,
        "share": f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(share_text)}",
    }
