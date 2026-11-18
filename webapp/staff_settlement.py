"""«Рассчитать» on the staff screen: the settlement editor of a table visit
(PQ-009, step 3). The service decides — ``apps/billing/visit_settlement.py``;
the screen lays the lines out and takes each guest's money with one tap."""

from __future__ import annotations

import uuid

from apps.billing.models import Payment
from apps.billing.visit_settlement import (
    SHARED,
    lay_out_visit,
    pay_for_guest,
    visit_settlement,
)
from core.i18n import N_, _
from webapp.context import WebAppDenied
from webapp.staff_billing import _billing
from webapp.staff_context import StaffRequest
from webapp.staff_views import staff_api
from webapp.views import _money, _text

PAY_METHODS = {"cash": Payment.Method.CASH, "terminal": Payment.Method.TERMINAL}


def _uuid(value) -> uuid.UUID:
    try:
        return uuid.UUID(hex=str(value))
    except ValueError as exc:
        raise WebAppDenied(400, N_("Обновите экран и попробуйте ещё раз.")) from exc


def _place(value) -> str:
    return SHARED if value == SHARED else str(_uuid(value))


def settlement_json(settlement) -> dict:
    return {
        "visit": settlement.visit.id.hex,
        "laid_out": settlement.laid_out,
        "guests": [
            {"id": guest.guest_id.hex, "label": guest.label, "bot": guest.has_bot,
             "here": guest.active, "to_pay": _money(guest.to_pay),
             "unbilled": _money(guest.unbilled)}
            for guest in settlement.guests
        ],
        "lines": [
            {"id": line.order_item_id.hex, "name": line.name, "quantity": line.quantity,
             "total": _money(line.total), "fixed": line.fixed,
             "place": line.place if line.place == SHARED else line.place.hex}
            for line in settlement.lines
        ],
        "sharers": [guest_id.hex for guest_id in settlement.sharers],
    }


@staff_api("GET")
def settlement_view(request, staff: StaffRequest, body: dict) -> dict:
    """``?visit=`` — the company's lines, where each is, what each guest pays."""

    _billing(staff)
    settlement = visit_settlement(partner_id=staff.partner.id,
                                  visit_id=_uuid(request.GET.get("visit", "")))
    return {"ok": True, "settlement": settlement_json(settlement)}


@staff_api("POST")
def settlement_save_view(request, staff: StaffRequest, body: dict) -> dict:
    """``{"visit", "places": {line: guest | "shared"}, "sharers": [guest]}`` — «Готово»."""

    _billing(staff)
    visit_id = _uuid(_text(body, "visit"))
    places = body.get("places")
    sharers = body.get("sharers")
    if not isinstance(places, dict) or not isinstance(sharers, list):
        raise WebAppDenied(400, N_("Обновите экран и попробуйте ещё раз."))
    lay_out_visit(
        partner_id=staff.partner.id, employee=staff.employee, visit_id=visit_id,
        places={str(_uuid(line)): _place(where) for line, where in places.items()},
        sharers=[str(_uuid(guest)) for guest in sharers],
    )
    settlement = visit_settlement(partner_id=staff.partner.id, visit_id=visit_id)
    return {"ok": True, "notice": _("Счета готовы."), "settlement": settlement_json(settlement)}


@staff_api("POST")
def settlement_pay_view(request, staff: StaffRequest, body: dict) -> dict:
    """``{"visit", "guest", "method": "cash" | "terminal", "amount"}`` — the
    guest's bill and parts of «Общее» at once; ``amount`` — what the screen showed."""

    _billing(staff)
    visit_id = _uuid(_text(body, "visit"))
    method = PAY_METHODS.get(_text(body, "method"))
    if method is None:
        raise WebAppDenied(400, N_("Обновите экран и попробуйте ещё раз."))
    pay_for_guest(
        partner_id=staff.partner.id, employee=staff.employee, visit_id=visit_id,
        guest_id=_uuid(_text(body, "guest")), method=method,
        expected_amount=_text(body, "amount") or None,
    )
    settlement = visit_settlement(partner_id=staff.partner.id, visit_id=visit_id)
    return {"ok": True, "notice": _("Оплачено."), "settlement": settlement_json(settlement)}
