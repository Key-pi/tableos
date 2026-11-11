"""The floor plan (PQ-052, step 1, owner 2026-11-11).

A venue has halls («Зал», «Тераса»); a hall is a grid of ``width`` ×
``height`` cells and a table takes a rectangle of cells in one hall, square
or round. ``save_hall_layout`` — the only writer of the plan fields of
``Table``: the hall is saved whole, under the lock of its row, so two people
arranging one hall never mix their layouts (lock order: ``Hall`` →
``Table``).

``table_states`` — what each table is doing now, for the plan in Admin and
on the staff screen: ``call`` (a guest called staff, nobody went yet),
``bill`` (a bill was asked for), ``busy`` (guests sit there), ``free``;
with reservations (step 3) a free table is ``reserved`` an hour before its
booking and ``late`` 15 minutes after its time with nobody seated, and every
table carries the booking it shows (``reservations.plan_bookings``).
A few queries for the whole venue, nothing stored.

A module of its own (``PartnerBotSettings.module_floor_plan_enabled``); the
adapters check it. No new import edges: ``tables`` already reads
``billing``, ``notifications`` and ``orders`` (AS-IS).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import CharField, F, Value
from django.utils import timezone

from apps.tables.models import TABLE_MAX_CELLS, Hall, Table, TableSession, TableVisit
from core.i18n import _

# A call older than this is not a call any more: the guest has long been
# helped (the same window as the staff screen's list of calls).
CALL_HOURS = 3
STATES = ("free", "reserved", "late", "busy", "bill", "call")


class FloorPlanError(ValueError):
    """A layout that cannot be saved; the message is for the person."""


def _parse(placement: dict, hall: Hall) -> tuple[UUID, int, int, int, int, str]:
    try:
        table_id = UUID(str(placement["table"]))
        x, y = int(placement["x"]), int(placement["y"])
        w, h = int(placement.get("w", 2)), int(placement.get("h", 2))
    except (KeyError, TypeError, ValueError) as exc:
        raise FloorPlanError(_("Схема пришла с ошибкой — обновите страницу.")) from exc
    shape = placement.get("shape", Table.Shape.SQUARE)
    if shape not in Table.Shape.values:
        raise FloorPlanError(_("Неизвестная форма стола."))
    if not (1 <= w <= TABLE_MAX_CELLS and 1 <= h <= TABLE_MAX_CELLS):
        raise FloorPlanError(_("Стол — от 1 до {cells} клеток в сторону.",
                               cells=TABLE_MAX_CELLS))
    if x < 0 or y < 0 or x + w > hall.width or y + h > hall.height:
        raise FloorPlanError(_("Стол выходит за границы зала «{hall}».", hall=hall.name))
    return table_id, x, y, w, h, shape


def _overlap(a, b) -> bool:
    return a[1] < b[1] + b[3] and b[1] < a[1] + a[3] and a[2] < b[2] + b[4] and b[2] < a[2] + a[4]


def save_hall_layout(*, partner_id, hall_id, placements: list[dict]) -> Hall:
    """Places the listed tables in the hall; tables of the hall left out leave
    the plan. Everything is checked before anything is written."""

    with transaction.atomic():
        hall = (
            Hall.objects.select_for_update(no_key=True)
            .filter(partner_id=partner_id, id=hall_id).first()
        )
        if hall is None:
            raise FloorPlanError(_("Зал не найден."))
        parsed = [_parse(placement, hall) for placement in placements]
        ids = [item[0] for item in parsed]
        if len(set(ids)) != len(ids):
            raise FloorPlanError(_("Один стол стоит на схеме дважды."))
        tables = {
            table.id: table
            for table in Table.objects.filter(partner_id=partner_id, id__in=ids)
        }
        if len(tables) != len(ids):
            raise FloorPlanError(_("Стол не найден — обновите страницу."))
        for index, first in enumerate(parsed):
            for second in parsed[index + 1:]:
                if _overlap(first, second):
                    raise FloorPlanError(_(
                        "Столы {first} и {second} стоят друг на друге.",
                        first=tables[first[0]].number, second=tables[second[0]].number,
                    ))
        Table.objects.filter(partner_id=partner_id, hall=hall).exclude(id__in=ids).update(
            hall=None, plan_x=None, plan_y=None
        )
        for table_id, x, y, w, h, shape in parsed:
            table = tables[table_id]
            table.hall, table.plan_x, table.plan_y = hall, x, y
            table.plan_w, table.plan_h, table.shape = w, h, shape
            table.save(update_fields=["hall", "plan_x", "plan_y", "plan_w", "plan_h", "shape",
                                      "updated_at"])
    return hall


def table_states(partner_id, *, now=None, reservations=None) -> dict[UUID, dict]:
    """``{table id: {"state", "guests", "since", "to_pay", "reservation"}}`` for
    every active table of the venue. ``reservations`` — whether bookings are
    shown (``None``: as the venue's module says)."""

    from apps.billing.models import Bill
    from apps.billing.repositories import BillingOperationsRepository
    from apps.tables.reservations import plan_bookings, plan_json, reservations_on

    now = now or timezone.now()
    states = {
        table_id: {"state": "free", "guests": 0, "since": None, "to_pay": Decimal("0"),
                   "reservation": None}
        for table_id in Table.objects.filter(partner_id=partner_id, is_active=True)
        .values_list("id", flat=True)
    }
    visits = TableVisit.objects.filter(partner_id=partner_id, status=TableVisit.Status.ACTIVE)
    for table_id, started_at in visits.values_list("table_id", "started_at"):
        state = states.get(table_id)
        if state is None:
            continue
        state["state"] = "busy"
        state["since"] = min(filter(None, (state["since"], started_at)))
    guests = defaultdict(int)
    for table_id in TableSession.objects.filter(
        partner_id=partner_id, status=TableSession.Status.ACTIVE,
        visit__status=TableVisit.Status.ACTIVE,
    ).values_list("table_id", flat=True):
        guests[table_id] += 1
    # Not billed yet — the same rule as the staff screen's «not billed» line.
    unbilled = BillingOperationsRepository.unbilled_open_orders_for_partner(partner_id).filter(
        table_session__visit__status=TableVisit.Status.ACTIVE,
    )
    for table_id, total in unbilled.values_list("table_id", "total_amount"):
        if table_id in states:
            states[table_id]["to_pay"] += total or 0
    for bill in Bill.objects.filter(
        partner_id=partner_id, visit__status=TableVisit.Status.ACTIVE,
        status__in=(Bill.Status.DRAFT, Bill.Status.ISSUED, Bill.Status.PARTIALLY_PAID),
    ).only("table_id", "total_amount", "paid_amount"):
        if bill.table_id in states:
            states[bill.table_id]["to_pay"] += max(
                Decimal("0"), (bill.total_amount or 0) - (bill.paid_amount or 0)
            )
    asked = set(_open_bill_requests(partner_id).values_list("table_id", flat=True))
    called = set(_open_calls(partner_id, now).values_list("table_session__table_id", flat=True))
    if reservations is None:
        reservations = reservations_on(partner_id)
    bookings = plan_bookings(partner_id, now=now) if reservations else {}
    for table_id, state in states.items():
        state["guests"] = guests.get(table_id, 0)
        if state["state"] == "busy":
            if table_id in called:
                state["state"] = "call"
            elif table_id in asked:
                state["state"] = "bill"
        if table_id in bookings:
            booking, kind = bookings[table_id]
            state["reservation"] = plan_json(booking, kind)
            if state["state"] == "free":
                state["state"] = kind
    return states


def _open_calls(partner_id, now):
    from apps.notifications.models import GuestCall

    return GuestCall.objects.filter(
        partner_id=partner_id, taken_at__isnull=True,
        created_at__gte=now - timedelta(hours=CALL_HOURS),
        table_session__visit__status=TableVisit.Status.ACTIVE,
    )


def _open_bill_requests(partner_id):
    from apps.billing.models import BillingRequest

    return BillingRequest.objects.filter(
        partner_id=partner_id, table_session__visit__status=TableVisit.Status.ACTIVE,
        status__in=(BillingRequest.Status.OPEN, BillingRequest.Status.AUTO_PREPARED),
    )


def table_alerts(partner_id, *, now=None, reservations=False) -> list[dict]:
    """Tables where a guest calls staff or asks for the bill — the «Столы»
    tab's red count on the staff screen, so it shows without opening the tab
    (owner 2026-11-11); with ``reservations`` also bookings 15 minutes late
    (``late``). One query, for the screen's every poll."""

    from apps.tables.reservations import late_bookings

    now = now or timezone.now()
    calls = _open_calls(partner_id, now).annotate(
        number=F("table_session__table__number"), kind=Value("call", output_field=CharField()),
    ).order_by().values_list("number", "kind")
    bills = _open_bill_requests(partner_id).annotate(
        number=F("table__number"), kind=Value("bill", output_field=CharField()),
    ).order_by().values_list("number", "kind")
    parts = [bills]
    if reservations:
        parts.append(late_bookings(partner_id, now=now).annotate(
            number=F("table__number"), kind=Value("late", output_field=CharField()),
        ).order_by().values_list("number", "kind"))
    # One query: the screen asks every 8 seconds.
    alerts = set(calls.union(*parts))
    return [{"table": number, "kind": kind} for number, kind in sorted(alerts)]


def _table_json(table: Table, state: dict) -> dict:
    since = state["since"]
    return {
        "id": table.id.hex,
        "number": table.number,
        "name": table.name,
        "seats": table.seats,
        "x": table.plan_x,
        "y": table.plan_y,
        "w": table.plan_w,
        "h": table.plan_h,
        "shape": table.shape,
        "state": state["state"],
        "guests": state["guests"],
        "since": timezone.localtime(since).strftime("%H:%M") if since else "",
        "to_pay": f"{state['to_pay']:.2f}",
        "reservation": state.get("reservation"),
    }


def floor_plan(partner_id, *, now=None) -> dict:
    """The halls with their tables and states, and the active tables not on
    the plan yet — for the plan pages of Admin and the staff screen."""

    states = table_states(partner_id, now=now)
    halls = list(Hall.objects.filter(partner_id=partner_id))
    placed: dict[UUID, list] = defaultdict(list)
    unplaced = []
    for table in Table.objects.filter(partner_id=partner_id, is_active=True).order_by("number"):
        entry = _table_json(table, states[table.id])
        if table.hall_id and table.plan_x is not None:
            placed[table.hall_id].append(entry)
        else:
            unplaced.append(entry)
    return {
        "halls": [
            {"id": hall.id.hex, "name": hall.name, "width": hall.width, "height": hall.height,
             "tables": placed.get(hall.id, [])}
            for hall in halls
        ],
        "unplaced": unplaced,
    }
