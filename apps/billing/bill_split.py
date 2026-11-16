"""A bill paid in equal parts (PQ-009, step 1; owner 2026-11-16).

One bill, N people — one hookah for three: the staff split the bill into N
equal parts and take each part by cash or card (``record_payment`` with
``split_part``); the bill is paid when every part is. The unpaid parts share
what is left to pay equally and the last takes the rounding, so a bill
changed or partly paid otherwise still adds up. A part is paid once (the
constraint ``unique_paid_split_part_per_bill``); once a part is paid the bill
is not split again. A full payment of the rest still closes a split bill.

Who: the owner, a manager, a cashier of this venue — as the staff screens
take payments. Lock: the ``Bill`` row, as every payment (``locks.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.billing.models import Bill, Payment
from apps.billing.services import BillingServiceError, recalculate_bill_totals, record_payment
from core.i18n import _

SPLIT_MAX_PARTS = 20
SPLIT_ROLES = frozenset({"owner", "manager", "cashier"})
PART_METHODS = (Payment.Method.CASH, Payment.Method.TERMINAL)
OPEN_STATUSES = (Bill.Status.DRAFT, Bill.Status.ISSUED, Bill.Status.PARTIALLY_PAID)
CENT = Decimal("0.01")


class BillSplitError(BillingServiceError):
    """The split or the part cannot be done; the message is for the staff."""


@dataclass(frozen=True)
class SplitPart:
    number: int
    amount: Decimal
    paid: bool
    method: str = ""


def may_split_bills(employee, partner_id) -> bool:
    return bool(
        employee is not None and employee.partner_id == partner_id
        and employee.is_active and employee.user.is_active
        and employee.user.role in SPLIT_ROLES
    )


def _equal(total: Decimal, count: int) -> list[Decimal]:
    if count <= 0:
        return []
    # Rounded down: the last part takes the kopecks over.
    share = (total / count).quantize(CENT, rounding=ROUND_DOWN)
    return [share] * (count - 1) + [total - share * (count - 1)]


def split_parts_of(bill: Bill) -> list[SplitPart]:
    """The parts as the staff see them: a paid part is its payment; the rest
    share what is left to pay. Reads the bill's loaded totals."""

    if not bill.split_parts:
        return []
    paid = {
        payment.split_part: payment
        for payment in bill.payments.all()
        if payment.split_part and payment.status == Payment.Status.PAID
    }
    remaining = max(Decimal("0.00"), Decimal(bill.total_amount) - Decimal(bill.paid_amount))
    unpaid = [number for number in range(1, bill.split_parts + 1) if number not in paid]
    shares = dict(zip(unpaid, _equal(remaining.quantize(CENT), len(unpaid)), strict=True))
    return [
        SplitPart(number, paid[number].amount, True, paid[number].method) if number in paid
        else SplitPart(number, shares[number], False)
        for number in range(1, bill.split_parts + 1)
    ]


def _locked_bill(partner_id, employee, bill_id) -> Bill:
    if not may_split_bills(employee, partner_id):
        raise BillSplitError(_("Делят счёт владелец, менеджер или кассир этого заведения."))
    try:
        bill = Bill.objects.select_for_update().prefetch_related("items", "payments").get(
            id=bill_id, partner_id=partner_id
        )
    except (Bill.DoesNotExist, ValueError, ValidationError) as exc:
        raise BillSplitError(_("Счёт не найден — обновите экран.")) from exc
    if bill.status not in OPEN_STATUSES:
        raise BillSplitError(_("Счёт уже закрыт — обновите экран."))
    recalculate_bill_totals(bill)
    return bill


@transaction.atomic
def split_bill_equally(*, partner_id, employee, bill_id, parts) -> Bill:
    """Split into ``parts`` equal parts (2–20), again into another number, or
    undo with 0 — until a part is paid."""

    try:
        parts = int(parts)
    except (TypeError, ValueError) as exc:
        raise BillSplitError(_("Укажите, на сколько частей делить.")) from exc
    if parts != 0 and not 2 <= parts <= SPLIT_MAX_PARTS:
        raise BillSplitError(_("Делить можно на 2–{most} частей.", most=SPLIT_MAX_PARTS))
    bill = _locked_bill(partner_id, employee, bill_id)
    if any(part.paid for part in split_parts_of(bill)):
        raise BillSplitError(_("Часть уже оплачена — делить заново нельзя."))
    if parts and bill.total_amount - bill.paid_amount <= 0:
        raise BillSplitError(_("Делить нечего — счёт оплачен."))
    bill.split_parts = parts
    bill.save(update_fields=["split_parts", "updated_at"])
    return bill


@transaction.atomic
def pay_split_part(*, partner_id, employee, bill_id, part, method,
                   expected_amount=None) -> Payment:
    """Take part ``part`` by cash or card. ``expected_amount`` — the amount
    the screen showed: if the bill changed since, the staff look again."""

    if method not in PART_METHODS:
        raise BillSplitError(_("Часть оплачивают наличными или картой."))
    bill = _locked_bill(partner_id, employee, bill_id)
    if not bill.split_parts:
        raise BillSplitError(_("Этот счёт не делили на части — обновите экран."))
    parts = {found.number: found for found in split_parts_of(bill)}
    found = parts.get(part) if isinstance(part, int) else None
    if found is None:
        raise BillSplitError(_("Такой части нет — обновите экран."))
    if found.paid:
        raise BillSplitError(_("Эта часть уже оплачена."))
    if expected_amount is not None:
        try:
            expected = Decimal(expected_amount).quantize(CENT)
        except (InvalidOperation, TypeError, ValueError):
            expected = None
        if expected != found.amount:
            raise BillSplitError(_("Сумма части изменилась — обновите экран."))
    return record_payment(
        bill=bill, amount=found.amount, method=method, created_by=employee.user,
        comment=f"Part {part} of {bill.split_parts}.", split_part=part,
    )
