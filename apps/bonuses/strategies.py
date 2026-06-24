"""Loyalty layer: bonus accrual strategies and the custom-strategy registry.

This module owns *how* loyalty bonuses are calculated. Built-in strategies cover
cashback, visit, and milestone programs; partner-specific custom functions can be
registered in ``CUSTOM_BONUS_STRATEGIES`` and selected per program via
``BonusProgram.strategy_code`` without touching the accrual orchestration in
``services.py`` or any bot handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import InvalidOperation
from decimal import Decimal
from typing import Any
from typing import Callable

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


@dataclass(frozen=True, slots=True)
class BonusLogicOption:
    code: str
    label: str
    description: str
    program_type: str
    strategy_code: str = ""
    allowed_trigger_events: tuple[str, ...] = ()
    default_percent: Decimal = Decimal("0.00")
    default_fixed_amount: Decimal = Decimal("0.00")
    default_min_order_total: Decimal = Decimal("0.00")
    default_milestone_order_count: int = 0
    default_max_redeem_share: Decimal = Decimal("30.00")
    default_expires_in_days: int = 90
    default_config: dict[str, Any] | None = None


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
    completed_orders_count = context.guest.orders.filter(
        partner_id=program.partner_id,
        status=Order.Status.COMPLETED,
    ).count()
    if completed_orders_count % program.milestone_order_count != 0:
        return Decimal("0.00")
    return Decimal(program.fixed_amount)


def _normalize_tiered_cashback_config(config: dict[str, Any]) -> list[dict[str, Decimal]]:
    tiers = config.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise BonusServiceError(
            "Tiered cashback config must contain a non-empty `tiers` list."
        )

    normalized_tiers: list[dict[str, Decimal]] = []
    for index, raw_tier in enumerate(tiers, start=1):
        if not isinstance(raw_tier, dict):
            raise BonusServiceError(f"Tier #{index} must be an object.")
        try:
            min_total = Decimal(str(raw_tier.get("min_total", "0"))).quantize(
                Decimal("0.01")
            )
            percent = Decimal(str(raw_tier["percent"])).quantize(Decimal("0.01"))
        except (KeyError, InvalidOperation) as exc:
            raise BonusServiceError(
                f"Tier #{index} must define valid `min_total` and `percent` values."
            ) from exc
        if min_total < 0:
            raise BonusServiceError(f"Tier #{index} cannot have negative `min_total`.")
        if percent <= 0:
            raise BonusServiceError(f"Tier #{index} must have positive `percent`.")
        normalized_tiers.append({"min_total": min_total, "percent": percent})

    normalized_tiers.sort(key=lambda item: item["min_total"])
    return normalized_tiers


def _tiered_cashback_by_total(program: BonusProgram, context: BonusContext) -> Decimal:
    total = _purchase_total(context)
    if total <= 0:
        return Decimal("0.00")

    matched_percent = Decimal("0.00")
    for tier in _normalize_tiered_cashback_config(program.config or {}):
        if total >= tier["min_total"]:
            matched_percent = tier["percent"]

    if matched_percent <= 0:
        return Decimal("0.00")
    return (total * matched_percent / Decimal("100")).quantize(Decimal("0.01"))


BUILTIN_BONUS_STRATEGIES = {
    BonusProgram.ProgramType.CASHBACK: _cashback_bonus,
    BonusProgram.ProgramType.VISIT: _visit_bonus,
    BonusProgram.ProgramType.MILESTONE: _milestone_bonus,
}

# Partner-specific strategies can be registered here later without changing the
# shared accrual loop or pushing venue branches into bot handlers.
CUSTOM_BONUS_STRATEGIES = {
    "tiered_cashback_by_total": _tiered_cashback_by_total,
}


BONUS_LOGIC_OPTIONS: dict[str, BonusLogicOption] = {
    "cashback_percent": BonusLogicOption(
        code="cashback_percent",
        label="Cashback percent",
        description="Percent cashback from purchase total.",
        program_type=BonusProgram.ProgramType.CASHBACK,
        allowed_trigger_events=(
            BonusProgram.TriggerEvent.ORDER_COMPLETED,
            BonusProgram.TriggerEvent.MANUAL_PURCHASE,
        ),
        default_percent=Decimal("5.00"),
        default_max_redeem_share=Decimal("30.00"),
    ),
    "visit_fixed_bonus": BonusLogicOption(
        code="visit_fixed_bonus",
        label="Visit fixed bonus",
        description="Fixed bonus for a QR visit, usually once per day.",
        program_type=BonusProgram.ProgramType.VISIT,
        allowed_trigger_events=(BonusProgram.TriggerEvent.VISIT,),
        default_fixed_amount=Decimal("50.00"),
        default_config={"once_per_day": True},
    ),
    "milestone_every_n_orders": BonusLogicOption(
        code="milestone_every_n_orders",
        label="Every N-th paid order",
        description="Fixed reward for each N-th paid/completed order.",
        program_type=BonusProgram.ProgramType.MILESTONE,
        allowed_trigger_events=(BonusProgram.TriggerEvent.ORDER_COMPLETED,),
        default_fixed_amount=Decimal("200.00"),
        default_milestone_order_count=5,
    ),
    "tiered_cashback_by_total": BonusLogicOption(
        code="tiered_cashback_by_total",
        label="Tiered cashback by total",
        description="Custom cashback example: 5% by default and 7% from 1000+.",
        program_type=BonusProgram.ProgramType.CASHBACK,
        strategy_code="tiered_cashback_by_total",
        allowed_trigger_events=(
            BonusProgram.TriggerEvent.ORDER_COMPLETED,
            BonusProgram.TriggerEvent.MANUAL_PURCHASE,
        ),
        default_config={
            "tiers": [
                {"min_total": "0.00", "percent": "5.00"},
                {"min_total": "1000.00", "percent": "7.00"},
            ]
        },
        default_max_redeem_share=Decimal("30.00"),
    ),
}


def get_bonus_logic_option(code: str) -> BonusLogicOption:
    try:
        return BONUS_LOGIC_OPTIONS[code]
    except KeyError as exc:
        raise BonusServiceError(f"Unknown bonus logic option `{code}`.") from exc


def list_bonus_logic_choices() -> list[tuple[str, str]]:
    return [(option.code, option.label) for option in BONUS_LOGIC_OPTIONS.values()]


def infer_bonus_logic_code(program: BonusProgram) -> str:
    if program.strategy_code == "tiered_cashback_by_total":
        return "tiered_cashback_by_total"
    if program.program_type == BonusProgram.ProgramType.CASHBACK:
        return "cashback_percent"
    if program.program_type == BonusProgram.ProgramType.VISIT:
        return "visit_fixed_bonus"
    if program.program_type == BonusProgram.ProgramType.MILESTONE:
        return "milestone_every_n_orders"
    return "cashback_percent"


def validate_bonus_logic_config(logic_code: str, config: dict[str, Any]) -> dict[str, Any]:
    if logic_code == "tiered_cashback_by_total":
        tiers = _normalize_tiered_cashback_config(config)
        return {
            "tiers": [
                {
                    "min_total": f"{tier['min_total']:.2f}",
                    "percent": f"{tier['percent']:.2f}",
                }
                for tier in tiers
            ]
        }
    return config


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
