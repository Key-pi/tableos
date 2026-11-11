"""Bills and tables on the Mini App's staff screen (PQ-048, stage 2).

The same as the bot's «Счета и оплаты» and «Столы» (PQ-044): who may use
them (``bot/services/staff_rights.py``), what a bill card offers
(``apps/billing/staff_actions.py:staff_bill_actions``) and the services they
call (``issue_bill``, ``record_payment``, ``pay_bill_with_bonuses``,
``create_shared_bill_for_table`` / ``create_personal_bills_for_table``,
``close_table_visit``, billing requests). Changing and voiding a bill and
refunds stay in the bot: they take reasons and comments.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.utils import timezone

from apps.billing import staff_actions as actions
from apps.billing.models import Bill, BillAdjustment, BillingRequest, Payment, Refund
from apps.billing.repositories import (
    BillingOperationsRepository,
    BillingRequestRepository,
    BillRepository,
)
from apps.billing.services import (
    cancel_order,
    get_bill_context_label,
    get_order_refund_lines,
    issue_bill,
    pay_bill_with_bonuses,
    preview_refund,
    record_payment,
    refund_bill_lines,
    refund_can_restock,
    remove_bill_lines,
    user_can_adjust_bills,
    void_bill,
)
from apps.billing.staff_actions import (
    bill_bonus_offer,
    bill_change_rights,
    staff_bill_actions,
)
from apps.billing.table_bills import (
    cancel_billing_request,
    create_personal_bills_for_table,
    create_shared_bill_for_table,
    mark_billing_request_processed,
)
from apps.bonuses.services import bonus_balance
from apps.orders.models import Order
from apps.orders.repositories import OrderRepository
from apps.partners.models import PartnerBotSettings
from apps.tables.floor_plan import floor_plan
from apps.tables.models import TableVisit
from apps.tables.services import close_table_visit
from bot.services.staff_rights import can_manage_billing, can_view_tables
from core.i18n import N_, _
from webapp.context import WebAppDenied
from webapp.staff_context import StaffRequest
from webapp.staff_views import staff_api
from webapp.views import _money, _text

BILLING_ONLY = N_("Работа со счетами доступна только владельцу, менеджеру или кассиру.")
TABLES_ONLY = N_(
    "Работа со столами доступна только владельцу, менеджеру или кассиру при "
    "включённом модуле столов.",
)

BILL_ACTION_LABELS = {
    actions.BILL_ISSUE: N_("Выдать счёт"),
    actions.BILL_PAY_BONUS: N_("Оплатить бонусами"),
    actions.BILL_PAY_CASH: N_("Наличные"),
    actions.BILL_PAY_TERMINAL: N_("Терминал"),
}
REQUEST_TYPE_LABELS = {
    BillingRequest.RequestType.PERSONAL: N_("Счёт на гостя"),
    BillingRequest.RequestType.SHARED: N_("Счёт на весь стол"),
    BillingRequest.RequestType.CUSTOM_SPLIT: N_("Нестандартное разделение"),
}


def _billing(staff: StaffRequest) -> None:
    if not can_manage_billing(staff.employee, staff.content):
        raise WebAppDenied(403, BILLING_ONLY, code="no_right")


def _tables(staff: StaffRequest) -> None:
    if not can_view_tables(staff.employee, staff.content):
        raise WebAppDenied(403, TABLES_ONLY, code="no_right")


def _place(bill: Bill) -> str:
    if bill.table_id is not None:
        return _("Стол {number}", number=bill.table.number)
    return bill.label or get_bill_context_label(bill)


# Reasons offered for changing a bill (as in the bot, PQ-028).
CHANGE_REASONS = (
    BillAdjustment.ReasonCode.INPUT_ERROR,
    BillAdjustment.ReasonCode.GUEST_REFUSED,
    BillAdjustment.ReasonCode.COMPLIMENT,
    BillAdjustment.ReasonCode.OTHER,
)


def _choices(codes, enum) -> list[dict]:
    return [{"code": code, "label": str(enum(code).label)} for code in codes]


def _bill_json(bill: Bill, *, full: bool = False, actor=None) -> dict:
    remaining = (bill.total_amount - bill.paid_amount).quantize(Decimal("0.01"))
    data = {
        "id": bill.public_id,
        "place": _place(bill),
        "status": bill.status,
        "status_label": bill.get_status_display(),
        "total": _money(bill.total_amount),
        "remaining": _money(remaining),
    }
    if not full:
        return data
    items = list(bill.items.all())
    offer = bill_bonus_offer(bill)
    can_edit, can_void = bill_change_rights(bill, actor) if actor is not None else (False, False)
    active = [item for item in items if item.is_active]
    orders = {
        order.id: order.public_id
        for order in Order.objects.filter(id__in={item.order_id for item in active})
    }
    data.update(
        can_edit=can_edit,
        can_void=can_void,
        reasons=_choices(CHANGE_REASONS, BillAdjustment.ReasonCode) if can_edit or can_void else [],
        orders=list(dict.fromkeys(orders[item.order_id] for item in active)) if can_edit else [],
        kind=bill.get_kind_display(),
        guest=bill.primary_guest.staff_label() if bill.primary_guest_id else _("несколько гостей"),
        lines=[
            {
                "id": str(item.order_item_id), "order": orders.get(item.order_id, ""),
                "name": item.item_name, "quantity": item.quantity,
                "total": _money(item.line_total),
            }
            for item in active
        ],
        removed=sum(1 for item in items if not item.is_active),
        paid=_money(bill.paid_amount),
        bonus_spent=_money(bill.bonus_spent_amount),
        bonus_offer=_money(offer) if offer > 0 else "",
        bonus_balance=_money(bonus_balance(bill.primary_guest)) if offer > 0 else "",
        label=bill.label,
        actions=[
            {
                "code": code,
                "label": _(BILL_ACTION_LABELS[code]),
                "style": "primary" if code != actions.BILL_ISSUE else "",
            }
            for code in staff_bill_actions(bill)
        ],
    )
    return data


def _request_json(billing_request: BillingRequest) -> dict:
    can_process = (
        billing_request.request_type == BillingRequest.RequestType.CUSTOM_SPLIT
        and billing_request.status
        in {BillingRequest.Status.OPEN, BillingRequest.Status.AUTO_PREPARED}
    )
    return {
        "id": billing_request.id.hex,
        "table": billing_request.table.number,
        "guest": billing_request.guest.staff_label(),
        "type": _(REQUEST_TYPE_LABELS.get(billing_request.request_type, "")),
        "status_label": billing_request.get_status_display(),
        "note": billing_request.note,
        "bill": billing_request.bill.public_id if billing_request.bill_id else "",
        "can_process": can_process,
    }


@staff_api("GET")
def bills_view(request, staff: StaffRequest, body: dict) -> dict:
    _billing(staff)
    partner_id = staff.partner.id
    return {
        "requests": [
            _request_json(item) for item in BillingRequestRepository.open_for_partner(partner_id)
        ],
        "bills": [_bill_json(bill) for bill in BillRepository.open_for_partner(partner_id)],
        "unbilled": BillingOperationsRepository.unbilled_open_orders_for_partner(
            partner_id
        ).count(),
    }


def _load_bill(staff: StaffRequest, public_id: str) -> Bill:
    try:
        return BillRepository.by_public_id_for_partner(staff.partner.id, public_id)
    except Bill.DoesNotExist as exc:
        raise WebAppDenied(404, N_("Счёт не найден.")) from exc


@staff_api("GET")
def bill_view(request, staff: StaffRequest, body: dict) -> dict:
    _billing(staff)
    bill = _load_bill(staff, request.GET.get("id", ""))
    return {"bill": _bill_json(bill, full=True, actor=staff.employee.user)}


@staff_api("POST")
def bill_action_view(request, staff: StaffRequest, body: dict) -> dict:
    _billing(staff)
    bill = _load_bill(staff, _text(body, "bill"))
    code = _text(body, "action")
    if code not in staff_bill_actions(bill):
        return {
            "ok": False, "stale": True,
            "bill": _bill_json(bill, full=True, actor=staff.employee.user),
        }
    actor = staff.employee.user
    notice = _("Оплата зафиксирована")
    if code == actions.BILL_ISSUE:
        issue_bill(bill)
        notice = _("Счёт выдан")
    elif code == actions.BILL_PAY_BONUS:
        result = pay_bill_with_bonuses(
            bill=bill, created_by=actor, comment="Paid via Mini App with bonus balance."
        )
        notice = _(
            "Списано бонусами: {bonus_spent_amount} грн. Остаток к оплате: "
            "{remaining_amount} грн.",
            bonus_spent_amount=result.bonus_spent_amount,
            remaining_amount=result.remaining_amount,
        ) if result.remaining_amount > 0 else _("Счёт полностью оплачен бонусами")
    else:
        method = Payment.Method.CASH if code == actions.BILL_PAY_CASH else Payment.Method.TERMINAL
        record_payment(
            bill=bill,
            amount=(bill.total_amount - bill.paid_amount).quantize(Decimal("0.01")),
            method=method,
            created_by=actor,
            comment=f"Recorded via Mini App ({method}).",
        )
    bill = BillRepository.all_for_partner(staff.partner.id).get(id=bill.id)
    return {
        "ok": True, "notice": notice,
        "bill": _bill_json(bill, full=True, actor=staff.employee.user),
    }


@staff_api("POST")
def bill_change_view(request, staff: StaffRequest, body: dict) -> dict:
    """Change a bill as in the bot (PQ-028): ``{"bill", "action": "remove_line" |
    "cancel_order" | "void", "line" | "order", "reason", "comment"}``. Who may
    and what may change the services decide under the bill lock."""

    _billing(staff)
    bill = _load_bill(staff, _text(body, "bill"))
    actor = staff.employee.user
    reason = _text(body, "reason")
    if reason not in CHANGE_REASONS:
        raise WebAppDenied(400, N_("Выберите причину."))
    comment = _text(body, "comment")
    action = _text(body, "action")
    if action == "remove_line":
        line_id = _text(body, "line")
        if not any(str(item.order_item_id) == line_id and item.is_active
                   for item in bill.items.all()):
            raise WebAppDenied(400, N_("Позиция уже убрана из счёта."))
        remove_bill_lines(
            bill=bill, order_item_ids=[line_id], actor=actor,
            reason_code=reason, reason_text=comment,
        )
        notice = _("Позиция убрана из счёта")
    elif action == "cancel_order":
        public_id = _text(body, "order")
        if not any(item.is_active and item.order.public_id == public_id
                   for item in bill.items.select_related("order")):
            raise WebAppDenied(400, N_("Заказа уже нет в открытом счёте."))
        order = OrderRepository.by_public_id_for_partner(staff.partner.id, public_id)
        cancel_order(order=order, actor=actor, reason_code=reason, reason_text=comment)
        notice = _("Заказ #{order_public_id} отменён", order_public_id=public_id)
    elif action == "void":
        void_bill(bill=bill, actor=actor, reason_code=reason, reason_text=comment)
        notice = _("Счёт аннулирован")
    else:
        raise WebAppDenied(400, "unknown action")
    bill = BillRepository.all_for_partner(staff.partner.id).get(id=bill.id)
    return {"ok": True, "notice": notice, "bill": _bill_json(bill, full=True, actor=actor)}


# --- refunds of a paid order (PQ-037) ------------------------------------------

REFUND_REASONS = tuple(Refund.ReasonCode.values)
REFUND_METHODS = tuple(Refund.Method.values)


def _refund_order(staff: StaffRequest, public_id: str) -> tuple[Order, Bill, list]:
    try:
        order = OrderRepository.by_public_id_for_partner(staff.partner.id, public_id)
    except Order.DoesNotExist as exc:
        raise WebAppDenied(404, N_("Заказ не найден.")) from exc
    if not user_can_adjust_bills(staff.employee.user, staff.partner.id):
        raise WebAppDenied(
            403, N_("Нет права изменять счета. Его выдаёт владелец заведения."), code="no_right"
        )
    bill, lines = get_order_refund_lines(order)
    if bill is None:
        raise WebAppDenied(400, N_("У заказа нет оплаченного счёта — возвращать нечего."))
    return order, bill, lines


@staff_api("GET")
def refund_view(request, staff: StaffRequest, body: dict) -> dict:
    order, _bill, lines = _refund_order(staff, request.GET.get("order", ""))
    return {
        "order": order.public_id,
        "lines": [
            {
                "id": line.id, "name": line.item_name, "quantity": line.quantity,
                "total": _money(line.line_total), "refunded": line.refunded,
            }
            for line in lines
        ],
        "reasons": _choices(REFUND_REASONS, Refund.ReasonCode),
        "methods": _choices(REFUND_METHODS, Refund.Method),
    }


def _chosen_lines(body: dict, lines) -> list[str]:
    wanted = body.get("lines")
    if not isinstance(wanted, list):
        raise WebAppDenied(400, N_("Отметьте, что вернуть."))
    open_ids = {line.id for line in lines if not line.refunded}
    chosen = [line_id for line_id in wanted if isinstance(line_id, str) and line_id in open_ids]
    if not chosen:
        raise WebAppDenied(400, N_("Отметьте, что вернуть."))
    return chosen


@staff_api("POST")
def refund_preview_view(request, staff: StaffRequest, body: dict) -> dict:
    _order, bill, lines = _refund_order(staff, _text(body, "order"))
    chosen = _chosen_lines(body, lines)
    money, bonus = preview_refund(bill, chosen)
    # «Вернуть на склад» is offered only when it would put something back
    # (catalog step 3, owner 2026-11-07).
    return {"money": _money(money), "bonus": _money(bonus),
            "can_restock": refund_can_restock(bill, chosen)}


@staff_api("POST")
def refund_do_view(request, staff: StaffRequest, body: dict) -> dict:
    _order, bill, lines = _refund_order(staff, _text(body, "order"))
    reason, method = _text(body, "reason"), _text(body, "method")
    if reason not in REFUND_REASONS or method not in REFUND_METHODS:
        raise WebAppDenied(400, N_("Выберите причину и как вернёте деньги."))
    refund = refund_bill_lines(
        bill=bill, bill_item_ids=_chosen_lines(body, lines), actor=staff.employee.user,
        reason_code=reason, method=method, reason_text=_text(body, "comment"),
        restock=body.get("restock") is True,
    )
    return {
        "ok": True,
        "notice": _(
            "Возврат оформлен: {money} грн, бонусов {bonus}.",
            money=refund.money_amount, bonus=refund.bonus_returned,
        ),
    }


@staff_api("POST")
def request_action_view(request, staff: StaffRequest, body: dict) -> dict:
    """A guest's bill request: «обработан» (a custom split) or cancelled."""

    _billing(staff)
    try:
        billing_request = BillingRequestRepository.by_id_for_partner(
            staff.partner.id, uuid.UUID(hex=_text(body, "request"))
        )
    except (ValueError, BillingRequest.DoesNotExist) as exc:
        raise WebAppDenied(404, N_("Запрос счёта не найден.")) from exc
    action = _text(body, "action")
    if action == "process":
        mark_billing_request_processed(billing_request)
    elif action == "cancel":
        cancel_billing_request(billing_request)
    else:
        raise WebAppDenied(400, "unknown action")
    return {"ok": True}


# --- tables --------------------------------------------------------------------


@staff_api("GET")
def tables_view(request, staff: StaffRequest, body: dict) -> dict:
    """Tables with guests now: their visits, what is not billed yet, open bills."""

    _tables(staff)
    partner_id = staff.partner.id
    sessions = list(BillingOperationsRepository.active_sessions_for_partner(partner_id))
    unbilled = list(
        BillingOperationsRepository.unbilled_open_orders_for_partner(partner_id)
        .select_related("table_session")
    )
    bills = list(BillRepository.open_for_partner(partner_id).exclude(table_id=None))
    visits = list(
        TableVisit.objects.filter(partner_id=partner_id, status=TableVisit.Status.ACTIVE)
        .select_related("table")
        .order_by("table__number", "started_at")
    )
    can_bill = can_manage_billing(staff.employee, staff.content)
    tables: dict = {}
    for visit in visits:
        table = tables.setdefault(
            visit.table_id,
            {"id": visit.table_id.hex, "number": visit.table.number, "visits": []},
        )
        visit_orders = [
            order for order in unbilled
            if order.table_session_id and order.table_session.visit_id == visit.id
        ]
        table["visits"].append({
            "id": visit.id.hex,
            "since": timezone.localtime(visit.started_at).strftime("%H:%M"),
            "guests": [
                session.guest.staff_label() for session in sessions if session.visit_id == visit.id
            ],
            "unbilled": len(visit_orders),
            "unbilled_total": _money(sum(order.total_amount for order in visit_orders)),
            "bills": [_bill_json(bill) for bill in bills if bill.visit_id == visit.id],
            "can_bill": can_bill and bool(visit_orders),
        })
    result = {"tables": sorted(tables.values(), key=lambda table: table["number"])}
    # PQ-052: with the floor plan module, the halls and every table's state;
    # a table with guests opens its card above by its id.
    if PartnerBotSettings.objects.filter(
        partner_id=partner_id, module_floor_plan_enabled=True
    ).exists():
        result["plan"] = floor_plan(partner_id)
    return result


@staff_api("POST")
def visit_action_view(request, staff: StaffRequest, body: dict) -> dict:
    """``{"visit": …, "action": "shared" | "personal" | "close"}``."""

    _tables(staff)
    try:
        visit = TableVisit.objects.select_related("table").get(
            partner_id=staff.partner.id, id=uuid.UUID(hex=_text(body, "visit"))
        )
    except (ValueError, TableVisit.DoesNotExist) as exc:
        raise WebAppDenied(404, N_("Визит не найден.")) from exc
    action = _text(body, "action")
    if action in {"shared", "personal"}:
        _billing(staff)
        if visit.status != TableVisit.Status.ACTIVE:
            raise WebAppDenied(400, N_("Этот визит уже закрыт."))
        builder = (
            create_shared_bill_for_table if action == "shared" else create_personal_bills_for_table
        )
        builder(partner_id=staff.partner.id, table_id=visit.table_id, visit_id=visit.id)
        return {"ok": True, "notice": _("Счёт визита подготовлен")}
    if action == "close":
        close_table_visit(visit, actor=staff.employee.user)
        return {"ok": True, "notice": _("Визит закрыт")}
    raise WebAppDenied(400, "unknown action")
