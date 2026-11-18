"""The settlement editor of a table visit (PQ-009, step 3; owner 2026-11-18).

«Рассчитать» on a table lays the company's lines out by who pays: each line,
whole, goes to a guest's own bill or to «Общее», which the guests who share
it pay in equal parts (``BillSplitShare``: part ``number`` is ``guest``'s).
«Готово» makes the bills in one command; a guest then pays their bill and
their part of «Общее» with one tap (``pay_for_guest``).

- The lines of a bill with money in it (paid in part or in full) stay where
  they are; the other bills of the visit are laid out again — cancelled with
  a ``BillAdjustment`` of kind ``relayout`` and their lines given to the new
  bills.
- The cashback is theirs who pay (``services._award_bill_cashback``); an
  order is paid when every bill with its lines is.

Who: the owner, a manager, a cashier of this venue — as the staff screens
take payments (``bill_split.may_split_bills``). Locks: the visit's open bills
in id order → their lines → orders and order lines (``create_bill_from_lines``)
→ as every payment (``record_payment``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.billing.bill_split import may_split_bills, split_parts_of
from apps.billing.models import (
    Bill,
    BillAdjustment,
    BillingRequest,
    BillItem,
    BillSplitShare,
    Payment,
)
from apps.billing.services import (
    BillingServiceError,
    _release_bill_lines,
    create_bill_from_lines,
    issue_bill,
    record_payment,
)
from apps.orders.models import Order, OrderItem
from apps.tables.models import TableVisit
from apps.tables.services import visit_guests
from core.i18n import _

SHARED = "shared"
OPEN = (Bill.Status.DRAFT, Bill.Status.ISSUED, Bill.Status.PARTIALLY_PAID)
PAY_METHODS = (Payment.Method.CASH, Payment.Method.TERMINAL)
CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class SettlementError(BillingServiceError):
    """The layout or the payment cannot be done; the message is for the staff."""


@dataclass(frozen=True)
class SettlementLine:
    order_item_id: UUID
    name: str
    quantity: int
    total: Decimal
    ordered_by: UUID
    place: UUID | str  # a guest's id or SHARED
    fixed: bool  # in a bill with money in it
    bill_id: UUID | None


@dataclass(frozen=True)
class SettlementGuest:
    guest_id: UUID
    session_id: UUID
    label: str
    has_bot: bool
    active: bool
    to_pay: Decimal  # their open bill and their unpaid parts of «Общее»
    unbilled: Decimal  # their lines not in a bill yet, as the editor starts


@dataclass(frozen=True)
class Settlement:
    visit: TableVisit
    guests: list[SettlementGuest]
    lines: list[SettlementLine]
    sharers: list[UUID]
    laid_out: bool


def _has_money(bill: Bill) -> bool:
    return (
        bill.status == Bill.Status.PAID or bill.paid_amount > 0 or bill.bonus_spent_amount > 0
        or any(payment.status == Payment.Status.PAID for payment in bill.payments.all())
    )


def _shares(bill: Bill) -> dict[int, UUID]:
    return {share.number: share.guest_id for share in bill.split_shares.all()}


def _read(visit: TableVisit, bills: list[Bill]) -> Settlement:
    """The visit's lines and guests as the editor shows them; ``bills`` —
    the visit's bills that are not cancelled (locked by the commands)."""

    guests = visit_guests(visit)
    items = list(
        OrderItem.objects.filter(
            partner_id=visit.partner_id, order__table_session__visit_id=visit.id,
        ).exclude(order__status=Order.Status.CANCELED)
        .select_related("order").order_by("order__created_at", "created_at", "id")
    )
    by_id = {bill.id: bill for bill in bills}
    placed = {
        line.order_item_id: by_id[line.bill_id]
        for line in BillItem.objects.filter(
            partner_id=visit.partner_id, is_active=True, bill_id__in=by_id,
        )
    }
    lines = []
    for item in items:
        bill = placed.get(item.id)
        if bill is None:
            place = item.order.guest_id
        elif bill.split_shares.exists() or bill.primary_guest_id is None:
            place = SHARED
        else:
            place = bill.primary_guest_id
        lines.append(SettlementLine(
            order_item_id=item.id, name=item.item_name, quantity=item.quantity,
            total=(item.unit_price * item.quantity).quantize(CENT),
            ordered_by=item.order.guest_id, place=place,
            fixed=bill is not None and _has_money(bill), bill_id=bill.id if bill else None,
        ))
    to_pay = {guest.guest.id: ZERO for guest in guests}
    sharers: list[UUID] = []
    for bill in bills:
        if bill.status not in OPEN:
            continue
        shares = _shares(bill)
        if shares:
            sharers = [shares[number] for number in sorted(shares)]
            for part in split_parts_of(bill):
                owner = shares.get(part.number)
                if not part.paid and owner in to_pay:
                    to_pay[owner] += part.amount
        elif bill.primary_guest_id in to_pay:
            to_pay[bill.primary_guest_id] += max(ZERO, bill.total_amount - bill.paid_amount)
    unbilled = {guest.guest.id: ZERO for guest in guests}
    for line in lines:
        if line.bill_id is None and line.ordered_by in unbilled:
            unbilled[line.ordered_by] += line.total
    return Settlement(
        visit=visit,
        guests=[
            SettlementGuest(
                guest_id=guest.guest.id, session_id=guest.session_id, label=guest.label,
                has_bot=guest.has_bot, active=guest.active, to_pay=to_pay[guest.guest.id],
                unbilled=unbilled[guest.guest.id],
            )
            for guest in guests
        ],
        lines=lines,
        sharers=sharers,
        laid_out=any(bill.status in OPEN for bill in bills),
    )


def _visit(partner_id, visit_id, *, active=True) -> TableVisit:
    visit = TableVisit.objects.filter(partner_id=partner_id, id=visit_id).first()
    if visit is None or (active and visit.status != TableVisit.Status.ACTIVE):
        raise SettlementError(_("Эта компания уже ушла — обновите экран."))
    return visit


def _bills(visit: TableVisit, *, lock: bool) -> list[Bill]:
    query = Bill.objects.filter(partner_id=visit.partner_id, visit_id=visit.id).exclude(
        status=Bill.Status.CANCELED
    )
    if lock:
        # Only the open ones change; the paid ones are read as they are.
        locked = list(query.filter(status__in=OPEN).select_for_update().order_by("id"))
        paid = list(query.exclude(status__in=OPEN))
        bills = locked + paid
    else:
        bills = list(query)
    return list(
        Bill.objects.filter(id__in=[bill.id for bill in bills])
        .prefetch_related("payments", "split_shares").order_by("id")
    )


def visit_settlement(*, partner_id, visit_id) -> Settlement:
    """What the editor shows: read only, no locks."""

    visit = _visit(partner_id, visit_id, active=False)
    return _read(visit, _bills(visit, lock=False))


def _check(partner_id, employee) -> None:
    if not may_split_bills(employee, partner_id):
        raise SettlementError(_("Рассчитывают стол владелец, менеджер или кассир этого заведения."))


def _relayout(bill: Bill, actor) -> None:
    """A bill without money gives its lines to the new layout."""

    items = list(
        BillItem.objects.select_for_update()
        .filter(partner_id=bill.partner_id, bill_id=bill.id, is_active=True).order_by("id")
    )
    total_before = bill.total_amount
    _release_bill_lines(bill=bill, items=items, actor=actor)
    bill.status = Bill.Status.CANCELED
    bill.closed_at = timezone.now()
    bill.subtotal_amount = bill.total_amount = ZERO
    bill.save(update_fields=["status", "closed_at", "subtotal_amount", "total_amount",
                             "updated_at"])
    BillAdjustment.objects.create(
        partner_id=bill.partner_id, bill=bill, kind=BillAdjustment.Kind.RELAYOUT, actor=actor,
        reason_code=BillAdjustment.ReasonCode.OTHER,
        reason_text="Laid out again in the settlement editor.",
        total_before=total_before, total_after=ZERO,
        payload={"order_item_ids": [str(item.order_item_id) for item in items]},
    )


@transaction.atomic
def lay_out_visit(*, partner_id, employee, visit_id, places: dict, sharers: list) -> list[Bill]:
    """«Готово»: ``places`` — every line not paid for yet → a guest's id or
    ``SHARED``; ``sharers`` — the guests who share «Общее» (at least two).
    Returns the new bills, issued."""

    _check(partner_id, employee)
    visit = _visit(partner_id, visit_id)
    bills = _bills(visit, lock=True)
    state = _read(visit, bills)
    guests = {str(guest.guest_id): guest for guest in state.guests}
    movable = {str(line.order_item_id): line for line in state.lines if not line.fixed}
    fixed = {str(line.order_item_id) for line in state.lines if line.fixed}
    places = {str(key): str(value) for key, value in (places or {}).items()}
    if set(places) & fixed:
        raise SettlementError(_("Позиция уже оплачена — её не переносят. Обновите экран."))
    if set(places) - set(movable):
        raise SettlementError(_("Позиция не из этого стола — обновите экран."))
    if set(movable) - set(places):
        raise SettlementError(_("Разложите все позиции по гостям или в «Общее»."))
    if any(value != SHARED and value not in guests for value in places.values()):
        raise SettlementError(_("Этого гостя нет в компании — обновите экран."))
    shared_lines = [key for key, value in places.items() if value == SHARED]
    sharer_ids = list(dict.fromkeys(str(guest_id) for guest_id in sharers or []))
    if shared_lines:
        if len(sharer_ids) < 2:
            raise SettlementError(_("«Общее» делят хотя бы два гостя."))
        if any(guest_id not in guests for guest_id in sharer_ids):
            raise SettlementError(_("Этого гостя нет в компании — обновите экран."))

    released = [
        bill for bill in bills
        if bill.status in OPEN and not _has_money(bill)
        and any(str(line.order_item_id) in movable for line in bill.items.filter(is_active=True))
    ]
    for bill in released:
        _relayout(bill, employee.user)

    made: list[Bill] = []
    by_guest: dict = {}
    for guest in state.guests:
        lines = [key for key, value in places.items() if value == str(guest.guest_id)]
        if not lines:
            continue
        bill = create_bill_from_lines(
            partner_id=partner_id, order_item_ids=lines, kind=Bill.Kind.PERSONAL,
            primary_guest=_guest(partner_id, guest.guest_id),
            label=guest.label,
        )
        by_guest[guest.guest_id] = issue_bill(bill)
        made.append(by_guest[guest.guest_id])
    shared = None
    if shared_lines:
        ordered = [guest.guest_id for guest in state.guests if str(guest.guest_id) in sharer_ids]
        shared = create_bill_from_lines(
            partner_id=partner_id, order_item_ids=shared_lines, kind=Bill.Kind.SPLIT,
            label=str(_("Общее")),
        )
        BillSplitShare.objects.bulk_create([
            BillSplitShare(partner_id=partner_id, bill=shared, number=number, guest_id=guest_id)
            for number, guest_id in enumerate(ordered, start=1)
        ])
        shared.split_parts = len(ordered)
        shared.save(update_fields=["split_parts", "updated_at"])
        shared = issue_bill(shared)
        made.append(shared)

    # The guests' requests for the bill follow their lines; a guest whose
    # lines another pays has had their answer.
    from apps.billing.table_bills import mark_billing_request_processed

    for request in BillingRequest.objects.select_for_update().filter(
        partner_id=partner_id, bill__in=released,
    ).order_by("id"):
        request.bill = by_guest.get(request.guest_id) or shared
        request.save(update_fields=["bill", "updated_at"])
        if request.bill is None:
            mark_billing_request_processed(request)
    return [Bill.objects.get(id=bill.id) for bill in made]


def _guest(partner_id, guest_id):
    from apps.users.models import GuestProfile

    return GuestProfile.objects.get(partner_id=partner_id, id=guest_id)


@transaction.atomic
def pay_for_guest(*, partner_id, employee, visit_id, guest_id, method,
                  expected_amount=None) -> list[Payment]:
    """One tap for a guest: their open bill and their unpaid parts of
    «Общее», by cash or card. ``expected_amount`` — what the screen showed."""

    _check(partner_id, employee)
    if method not in PAY_METHODS:
        raise SettlementError(_("Гость платит наличными или картой."))
    visit = _visit(partner_id, visit_id, active=False)
    bills = [bill for bill in _bills(visit, lock=True) if bill.status in OPEN]
    try:
        guest_id = UUID(str(guest_id))
    except ValueError as exc:
        raise SettlementError(_("Этого гостя нет в компании — обновите экран.")) from exc
    plan: list[tuple[Bill, Decimal, int | None]] = []
    for bill in bills:
        shares = _shares(bill)
        if shares:
            plan.extend(
                (bill, part.amount, part.number) for part in split_parts_of(bill)
                if not part.paid and part.amount > 0 and shares.get(part.number) == guest_id
            )
        elif bill.primary_guest_id == guest_id and bill.total_amount > bill.paid_amount:
            plan.append((bill, bill.total_amount - bill.paid_amount, None))
    total = sum((amount for _bill, amount, _part in plan), ZERO)
    if total <= 0:
        raise SettlementError(_("Этому гостю платить нечего."))
    if expected_amount is not None:
        try:
            expected = Decimal(str(expected_amount)).quantize(CENT)
        except (InvalidOperation, ValueError):
            expected = None
        if expected != total:
            raise SettlementError(_("Сумма гостя изменилась — обновите экран."))
    return [
        record_payment(
            bill=bill, amount=amount, method=method, created_by=employee.user,
            comment=f"Guest's share of the table, part {part}." if part else "Guest's bill.",
            split_part=part,
        )
        for bill, amount, part in plan
    ]
