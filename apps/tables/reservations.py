"""Reservations (PQ-052, step 3, owner 2026-11-11).

Only staff book — on the staff screen and in Admin: name, phone, guests,
time, duration (2 hours by default), comment. The only writer of
``Reservation``.

- One table is never booked twice for overlapping time: a booking and a
  move lock the table row first (``no_key``) and look at the table's other
  bookings «Ждём» and «Пришли». Lock order: ``Table → Reservation``; seating
  then opens the visit through ``open_staff_table_session`` (same order).
- «Пришли» opens the table's visit with the company's anonymous guest (step
  2); a table where someone still sits is refused. «Не пришли» — by hand,
  once the time has come; «Отменить» — while «Ждём».
- On the plan (``floor_plan``): an hour before, a free table is «reserved»;
  15 minutes after the time with nobody seated, «late» — highlighted and on
  the «Столы» tab's red count; after the end it leaves the plan.

Who: the owner, a manager, a cashier, a waiter of this venue (the platform
too, from Admin). The module ``module_reservations_enabled`` needs the
floor plan.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.partners.models import PartnerBotSettings
from apps.tables.models import Reservation, Table, TableVisit
from core.i18n import _

RESERVATION_ROLES = frozenset({"owner", "manager", "cashier", "waiter"})
DEFAULT_MINUTES = 120
MIN_MINUTES, MAX_MINUTES = 30, 720
SHOWN_BEFORE = timedelta(minutes=60)  # the table shows «reserved» an hour before
LATE_AFTER = timedelta(minutes=15)  # and lights up 15 minutes after the time
# A booking a little in the past is still taken (the guests are at the door).
PAST_ALLOWED = timedelta(minutes=15)
# What keeps a table's time: a booking still «Ждём», and one whose company
# sits there now (once they leave, the rest of its time is free again).
HOLDING = Q(status=Reservation.Status.BOOKED) | Q(
    status=Reservation.Status.SEATED, visit__status=TableVisit.Status.ACTIVE
)


class ReservationError(ValueError):
    """A booking that cannot be made; the message is for the person."""


def venue_zone(partner) -> ZoneInfo:
    """Bookings are told and shown in the venue's own time."""

    return ZoneInfo(getattr(partner, "timezone", "") or settings.TIME_ZONE)


def _hm(moment, zone) -> str:
    return f"{moment.astimezone(zone):%H:%M}"


def reservations_on(partner_id) -> bool:
    return PartnerBotSettings.objects.filter(
        partner_id=partner_id, module_tables_enabled=True, module_floor_plan_enabled=True,
        module_reservations_enabled=True,
    ).exists()


def may_manage_reservations(user, partner_id) -> bool:
    from apps.partners.networks import acts_for_partner

    if user is None or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return user.role in RESERVATION_ROLES and acts_for_partner(user, partner_id)


def _check(partner_id, actor) -> None:
    if not may_manage_reservations(actor, partner_id):
        raise ReservationError(_(
            "Бронируют официант, кассир, менеджер или владелец этого заведения."
        ))
    if not reservations_on(partner_id):
        raise ReservationError(_("Бронь столов в этом заведении выключена."))


def _clean(*, starts_at: datetime, minutes, guests, name, phone, comment, now,
           kept_start=None) -> dict:
    """``kept_start`` — the booking's own time: a late booking keeps it while
    its phone or comment is fixed."""

    name, phone, comment = (name or "").strip(), (phone or "").strip(), (comment or "").strip()
    if not name:
        raise ReservationError(_("Укажите имя гостя."))
    if not phone:
        raise ReservationError(_("Укажите телефон гостя."))
    try:
        minutes, guests = int(minutes), int(guests)
    except (TypeError, ValueError) as exc:
        raise ReservationError(_("Проверьте длительность и число гостей.")) from exc
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise ReservationError(_("Бронь — от {low} до {high} минут.", low=MIN_MINUTES,
                                 high=MAX_MINUTES))
    if not 1 <= guests <= 99:
        raise ReservationError(_("Гостей — от 1 до 99."))
    if timezone.is_naive(starts_at):
        raise ReservationError(_("Укажите дату и время брони."))
    if starts_at < now - PAST_ALLOWED and starts_at != kept_start:
        raise ReservationError(_("Время брони уже прошло."))
    return {"starts_at": starts_at, "minutes": minutes, "guests": guests,
            "name": name[:80], "phone": phone[:32], "comment": comment[:300]}


def _lock_table(partner_id, table_id) -> Table:
    try:
        return Table.objects.select_for_update(of=("self",), no_key=True).get(
            id=table_id, partner_id=partner_id, is_active=True
        )
    except (Table.DoesNotExist, ValueError) as exc:
        raise ReservationError(_("Стол не найден в этом заведении.")) from exc


def _refuse_overlap(table: Table, starts_at, minutes, *, exclude_id=None) -> None:
    ends_at = starts_at + timedelta(minutes=minutes)
    other = (
        Reservation.objects.filter(HOLDING, partner_id=table.partner_id, table=table,
                                   starts_at__lt=ends_at, ends_at__gt=starts_at)
        .exclude(id=exclude_id).order_by("starts_at").first()
    )
    if other is not None:
        zone = venue_zone(table.partner)
        raise ReservationError(_(
            "Стол {table} уже забронирован с {start} до {end} ({name}).",
            table=table.number, start=_hm(other.starts_at, zone),
            end=_hm(other.ends_at, zone), name=other.name,
        ))


@transaction.atomic
def create_reservation(*, partner_id, actor, table_id, starts_at, minutes=DEFAULT_MINUTES,
                       guests, name, phone, comment="") -> Reservation:
    _check(partner_id, actor)
    fields = _clean(starts_at=starts_at, minutes=minutes, guests=guests, name=name,
                    phone=phone, comment=comment, now=timezone.now())
    table = _lock_table(partner_id, table_id)
    _refuse_overlap(table, fields["starts_at"], fields["minutes"])
    return Reservation.objects.create(partner_id=partner_id, table=table, created_by=actor,
                                      **fields)


def _locked_booking(partner_id, reservation_id) -> Reservation:
    """The booking under its lock, after its table's (``Table → Reservation``)."""

    current = Reservation.objects.filter(partner_id=partner_id, id=reservation_id).first()
    if current is None:
        raise ReservationError(_("Бронь не найдена — обновите экран."))
    _lock_table(partner_id, current.table_id)
    booking = Reservation.objects.select_for_update().get(id=current.id)
    if booking.table_id != current.table_id:
        raise ReservationError(_("Бронь только что изменили — обновите экран."))
    if booking.status != Reservation.Status.BOOKED:
        raise ReservationError(_("Бронь уже отмечена: «{status}». Обновите экран.",
                                 status=booking.get_status_display()))
    return booking


@transaction.atomic
def update_reservation(*, partner_id, actor, reservation_id, table_id, starts_at, minutes,
                       guests, name, phone, comment="") -> Reservation:
    """Another time, table, size or contact for a booking still «Ждём»."""

    _check(partner_id, actor)
    current = Reservation.objects.filter(partner_id=partner_id, id=reservation_id).first()
    if current is None:
        raise ReservationError(_("Бронь не найдена — обновите экран."))
    fields = _clean(starts_at=starts_at, minutes=minutes, guests=guests, name=name,
                    phone=phone, comment=comment, now=timezone.now(),
                    kept_start=current.starts_at)
    # Both tables, in id order, then the booking.
    target = None
    for table_key in sorted({str(current.table_id), str(table_id)}):
        locked = _lock_table(partner_id, table_key)
        if str(locked.id) == str(table_id):
            target = locked
    booking = Reservation.objects.select_for_update().get(id=current.id)
    if booking.status != Reservation.Status.BOOKED:
        raise ReservationError(_("Бронь уже отмечена: «{status}». Обновите экран.",
                                 status=booking.get_status_display()))
    _refuse_overlap(target, fields["starts_at"], fields["minutes"], exclude_id=booking.id)
    for field, value in fields.items():
        setattr(booking, field, value)
    booking.table = target
    booking.save()
    return booking


@transaction.atomic
def seat_reservation(*, partner_id, actor, reservation_id, visit_id=None) -> Reservation:
    """«Пришли»: the table's visit opens for this company.

    Somebody already sits there: refused — unless the screen asked «это гости
    брони?» and staff said yes (``visit_id`` — that company: they were seated
    before the booking was marked), then the booking joins their visit.
    Lock order: ``Table → Reservation``, then the visit as
    ``open_staff_table_session`` (its anonymous guest is a new row)."""

    from apps.tables.services import StaleTablePage, open_staff_table_session

    _check(partner_id, actor)
    booking = _locked_booking(partner_id, reservation_id)
    if visit_id is not None:
        visit = TableVisit.objects.filter(
            partner_id=partner_id, table_id=booking.table_id, id=visit_id,
            status=TableVisit.Status.ACTIVE,
        ).first()
        if visit is None:
            raise ReservationError(_("Эта компания уже ушла — обновите экран."))
    else:
        try:
            visit = open_staff_table_session(partner_id=partner_id,
                                             table_id=booking.table_id).visit
        except StaleTablePage as exc:
            raise ReservationError(_(
                "За столом {table} ещё сидят: закройте их визит или перенесите бронь "
                "на другой стол.", table=booking.table.number,
            )) from exc
    booking.status = Reservation.Status.SEATED
    booking.visit = visit
    booking.closed_by, booking.closed_at = actor, timezone.now()
    booking.save(update_fields=["status", "visit", "closed_by", "closed_at", "updated_at"])
    return booking


@transaction.atomic
def mark_reservation(*, partner_id, actor, reservation_id, status) -> Reservation:
    """«Не пришли» (once the time has come) or «Отменить»."""

    _check(partner_id, actor)
    if status not in (Reservation.Status.NO_SHOW, Reservation.Status.CANCELED):
        raise ReservationError(_("Неизвестное действие с бронью."))
    booking = _locked_booking(partner_id, reservation_id)
    if status == Reservation.Status.NO_SHOW and timezone.now() < booking.starts_at:
        raise ReservationError(_("Время брони ещё не наступило."))
    booking.status = status
    booking.closed_by, booking.closed_at = actor, timezone.now()
    booking.save(update_fields=["status", "closed_by", "closed_at", "updated_at"])
    return booking


def plan_bookings(partner_id, *, now=None) -> dict:
    """``{table id: (booking, "reserved" | "late")}`` — the booking the plan
    shows for each table: «Ждём», from an hour before its time until its end;
    «late» once 15 minutes have passed with nobody seated."""

    now = now or timezone.now()
    shown = {}
    for booking in Reservation.objects.filter(
        partner_id=partner_id, status=Reservation.Status.BOOKED,
        starts_at__lte=now + SHOWN_BEFORE, ends_at__gt=now,
    ).select_related("partner", "table").order_by("starts_at"):
        if booking.table_id in shown:
            continue
        shown[booking.table_id] = (
            booking, "late" if now >= booking.starts_at + LATE_AFTER else "reserved"
        )
    return shown


def late_bookings(partner_id, *, now=None):
    """Bookings 15 minutes past their time with nobody marked — for the
    «Столы» tab's red count (``floor_plan.table_alerts``)."""

    now = now or timezone.now()
    return Reservation.objects.filter(
        partner_id=partner_id, status=Reservation.Status.BOOKED,
        starts_at__lte=now - LATE_AFTER, ends_at__gt=now,
    )


def plan_json(booking: Reservation, kind: str) -> dict:
    """What a table on the plan says about its booking: the booking itself
    (the plan's sheet acts on it) and whether it is ``late``."""

    return {**booking_json(booking), "late": kind == "late"}


def booking_json(booking: Reservation) -> dict:
    """A booking for the staff screen's list (``table`` and ``partner`` joined)."""

    zone = venue_zone(booking.partner)
    local = booking.starts_at.astimezone(zone)
    return {
        "id": booking.id.hex,
        "table": booking.table.number,
        "table_id": booking.table_id.hex,
        "date": f"{local:%Y-%m-%d}",
        "time": f"{local:%H:%M}",
        "until": _hm(booking.ends_at, zone),
        "minutes": booking.minutes,
        "guests": booking.guests,
        "name": booking.name,
        "phone": booking.phone,
        "comment": booking.comment,
        "status": booking.status,
        "status_label": str(booking.get_status_display()),
        "started": timezone.now() >= booking.starts_at,
        "late": (booking.status == Reservation.Status.BOOKED
                 and booking.starts_at + LATE_AFTER <= timezone.now() < booking.ends_at),
    }
