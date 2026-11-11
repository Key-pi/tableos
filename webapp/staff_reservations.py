"""Reservations on the staff screen (PQ-052, step 3).

The «Столы» tab: the bookings of a day, the form, «Пришли», «Не пришли»,
«Отменить». Dates and times come and go in the venue's own time; everything
is decided by ``apps/tables/reservations.py`` (rights, the module, overlaps,
the table and the visit).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

from apps.tables.models import Reservation, Table
from apps.tables.reservations import (
    booking_json,
    create_reservation,
    mark_reservation,
    seat_reservation,
    update_reservation,
    venue_zone,
)
from bot.services.staff_rights import can_manage_reservations
from core.i18n import N_, _
from webapp.context import WebAppDenied
from webapp.staff_context import StaffRequest
from webapp.staff_views import staff_api
from webapp.views import _text


def _right(staff: StaffRequest) -> None:
    if not can_manage_reservations(staff.employee, staff.content):
        raise WebAppDenied(
            403, N_("Бронируют официант, кассир, менеджер или владелец."), code="no_right"
        )


def _uuid(value, message) -> uuid.UUID:
    try:
        return uuid.UUID(hex=str(value))
    except ValueError as exc:
        raise WebAppDenied(400, message) from exc


def _day(value: str, staff: StaffRequest) -> date:
    if not value:
        return datetime.now(venue_zone(staff.partner)).date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise WebAppDenied(400, N_("Укажите дату и время брони.")) from exc


@staff_api("GET")
def list_view(request, staff: StaffRequest, body: dict) -> dict:
    """``?date=YYYY-MM-DD`` (today by default): the day's bookings, every
    status, and the tables for the form."""

    _right(staff)
    zone = venue_zone(staff.partner)
    day = _day(request.GET.get("date", ""), staff)
    start = datetime.combine(day, time.min, tzinfo=zone)
    bookings = (
        Reservation.objects.filter(partner_id=staff.partner.id, starts_at__gte=start,
                                   starts_at__lt=start + timedelta(days=1))
        .select_related("table", "partner").order_by("starts_at", "table__number")
    )
    return {
        "date": day.isoformat(),
        "today": datetime.now(zone).date().isoformat(),
        "reservations": [booking_json(booking) for booking in bookings],
        "tables": [
            {"id": table.id.hex, "number": table.number, "seats": table.seats}
            for table in Table.objects.filter(partner_id=staff.partner.id, is_active=True)
            .order_by("number")
        ],
    }


def _starts_at(body: dict, staff: StaffRequest) -> datetime:
    try:
        moment = datetime.combine(date.fromisoformat(_text(body, "date")),
                                  time.fromisoformat(_text(body, "time")))
    except ValueError as exc:
        raise WebAppDenied(400, N_("Укажите дату и время брони.")) from exc
    return moment.replace(tzinfo=venue_zone(staff.partner))


def _number(body: dict, key: str) -> int:
    try:
        return int(body.get(key))
    except (TypeError, ValueError) as exc:
        raise WebAppDenied(400, N_("Проверьте длительность и число гостей.")) from exc


@staff_api("POST")
def save_view(request, staff: StaffRequest, body: dict) -> dict:
    """``{"id"?, "table", "date", "time", "minutes", "guests", "name",
    "phone", "comment"?}`` — a new booking, or another time for one."""

    _right(staff)
    fields = {
        "partner_id": staff.partner.id, "actor": staff.employee.user,
        "table_id": _uuid(_text(body, "table"), N_("Стол не найден.")),
        "starts_at": _starts_at(body, staff), "minutes": _number(body, "minutes"),
        "guests": _number(body, "guests"), "name": _text(body, "name"),
        "phone": _text(body, "phone"), "comment": _text(body, "comment"),
    }
    if _text(body, "id"):
        booking = update_reservation(
            reservation_id=_uuid(_text(body, "id"), N_("Бронь не найдена — обновите экран.")),
            **fields,
        )
        notice = _("Бронь изменена.")
    else:
        booking = create_reservation(**fields)
        notice = _("Стол {table} забронирован на {time}.", table=booking.table.number,
                   time=booking_json(booking)["time"])
    booking = Reservation.objects.select_related("table", "partner").get(id=booking.id)
    return {"ok": True, "notice": notice, "reservation": booking_json(booking)}


ACTIONS = {"no_show": Reservation.Status.NO_SHOW, "cancel": Reservation.Status.CANCELED}


@staff_api("POST")
def action_view(request, staff: StaffRequest, body: dict) -> dict:
    """``{"id", "action": "seat" | "no_show" | "cancel", "visit"?}`` — ``visit``
    with «seat»: staff said these are the booking's guests already seated."""

    _right(staff)
    reservation_id = _uuid(_text(body, "id"), N_("Бронь не найдена — обновите экран."))
    action = _text(body, "action")
    if action == "seat":
        visit = _text(body, "visit")
        booking = seat_reservation(
            partner_id=staff.partner.id, actor=staff.employee.user,
            reservation_id=reservation_id,
            visit_id=_uuid(visit, N_("Компания за столом не найдена.")) if visit else None,
        )
        notice = _("Стол {table}: гости пришли, визит открыт.", table=booking.table.number)
    elif action in ACTIONS:
        booking = mark_reservation(partner_id=staff.partner.id, actor=staff.employee.user,
                                   reservation_id=reservation_id, status=ACTIONS[action])
        notice = _("Бронь отмечена: «{status}».", status=booking.get_status_display())
    else:
        raise WebAppDenied(400, "unknown action")
    return {"ok": True, "notice": notice}
