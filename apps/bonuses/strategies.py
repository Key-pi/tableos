"""Loyalty layer: bonus accrual strategies and the custom-strategy registry.

This module owns *how* loyalty bonuses are calculated. Built-in strategies cover
cashback, visit, and milestone programs; partner-specific custom functions can be
registered in ``CUSTOM_BONUS_STRATEGIES`` and selected per program via
``BonusProgram.strategy_code`` without touching the accrual orchestration in
``services.py`` or any bot handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.orders.models import Order
from apps.users.models import GuestProfile


class BonusServiceError(Exception):
    """Raised when loyalty rules cannot be evaluated or applied safely."""


@dataclass(slots=True)
class BonusContext:
    event: str
    guest: GuestProfile
    order: Order | None = None
    purchase_total: Decimal | None = None
    comment: str = ""


def _purchase_total(context: BonusContext) -> Decimal:
    if context.purchase_total is not None:
        return Decimal(context.purchase_total)
    if context.order is not None:
        return Decimal(context.order.total_amount)
    return Decimal("0.00")


def _cashback_bonus(program: BonusProgram, context: BonusContext) -> Decimal:
    total = _purchase_total(context)
    if total < program.min_order_total:
        return Decimal("0.00")
    return (total * program.percent / Decimal("100")).quantize(Decimal("0.01"))


def _visit_bonus(program: BonusProgram, context: BonusContext) -> Decimal:
    once_per_day = program.config.get("once_per_day", True)
    if once_per_day:
        started_at = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        already_awarded = BonusTransaction.objects.filter(
            partner_id=program.partner_id,
            guest=context.guest,
            program=program,
            transaction_type=BonusTransaction.TransactionType.ACCRUAL,
            created_at__gte=started_at,
        ).exists()
        if already_awarded:
            return Decimal("0.00")
    return Decimal(program.fixed_amount)


def _milestone_bonus(program: BonusProgram, context: BonusContext) -> Decimal:
    if context.order is None or program.milestone_order_count <= 0:
        return Decimal("0.00")
    paid_orders_count = context.guest.orders.filter(
        partner_id=program.partner_id,
        paid_at__isnull=False,
    ).exclude(status=Order.Status.CANCELED).count()
    if paid_orders_count % program.milestone_order_count != 0:
        return Decimal("0.00")
    return Decimal(program.fixed_amount)


BUILTIN_BONUS_STRATEGIES = {
    BonusProgram.ProgramType.CASHBACK: _cashback_bonus,
    BonusProgram.ProgramType.VISIT: _visit_bonus,
    BonusProgram.ProgramType.MILESTONE: _milestone_bonus,
}

# Partner-specific strategies can be registered here later without changing the
# shared accrual loop or pushing venue branches into bot handlers.
CUSTOM_BONUS_STRATEGIES = {}


def resolve_bonus_strategy(program: BonusProgram):
    if program.strategy_code:
        try:
            return CUSTOM_BONUS_STRATEGIES[program.strategy_code]
        except KeyError as exc:
            raise BonusServiceError(
                "Unknown bonus strategy code "
                f"`{program.strategy_code}` for program `{program.name}`."
            ) from exc
    return BUILTIN_BONUS_STRATEGIES[program.program_type]
