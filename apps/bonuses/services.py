from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.bonuses.models import BonusProgram, BonusTransaction, WalkInSale, WalkInSaleItem
from apps.bonuses.repositories import BonusProgramRepository
from apps.bonuses.strategies import (
    BonusContext,
    BonusServiceError,
    resolve_bonus_strategy,
)
from apps.menu.models import MenuItem
from apps.orders.models import Order
from apps.users.models import GuestProfile
from apps.users.selectors import get_guest_profile_by_customer_code


@dataclass(slots=True)
class WalkInSalePreview:
    guest: GuestProfile | None
    items: list[dict]
    total_amount: Decimal
    projected_bonus_amount: Decimal
    redeemable_bonus_amount: Decimal = Decimal("0.00")


def get_bonus_ledger_balance(*, partner_id, guest_id) -> Decimal:
    """Calculate a guest balance from the immutable bonus ledger."""

    balance = Decimal("0.00")
    transactions = BonusTransaction.objects.filter(
        partner_id=partner_id,
        guest_id=guest_id,
    ).only("transaction_type", "amount")
    for bonus_transaction in transactions.iterator():
        amount = Decimal(bonus_transaction.amount).quantize(Decimal("0.01"))
        if bonus_transaction.transaction_type in {
            BonusTransaction.TransactionType.ACCRUAL,
            BonusTransaction.TransactionType.MANUAL,
        }:
            balance += amount
        else:
            balance -= abs(amount)
    return balance.quantize(Decimal("0.01"))


def assert_bonus_balance_reconciled(guest: GuestProfile) -> None:
    stored_balance = Decimal(guest.loyalty_balance).quantize(Decimal("0.01"))
    ledger_balance = get_bonus_ledger_balance(
        partner_id=guest.partner_id,
        guest_id=guest.id,
    )
    if stored_balance != ledger_balance:
        raise BonusServiceError(
            "Баланс бонусов требует сверки с историей операций перед изменением."
        )


def sync_bonus_balance_from_ledger(guest: GuestProfile) -> GuestProfile:
    ledger_balance = get_bonus_ledger_balance(
        partner_id=guest.partner_id,
        guest_id=guest.id,
    )
    if Decimal(guest.loyalty_balance).quantize(Decimal("0.01")) != ledger_balance:
        guest.loyalty_balance = ledger_balance
        guest.save(update_fields=["loyalty_balance", "updated_at"])
    return guest


def _parse_positive_money(value: Decimal | str, *, label: str) -> Decimal:
    try:
        amount = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BonusServiceError(f"{label} должна быть корректным денежным значением.") from exc
    if not amount.is_finite() or amount <= 0:
        raise BonusServiceError(f"{label} должна быть больше нуля.")
    return amount


@transaction.atomic
def apply_bonus_programs(
    *,
    partner_id,
    event: str,
    guest: GuestProfile,
    order: Order | None = None,
    bill=None,
    walk_in_sale: WalkInSale | None = None,
    purchase_total: Decimal | None = None,
    source_type: str = "",
    source_id: str = "",
    comment: str = "",
) -> list[BonusTransaction]:
    if guest.partner_id != partner_id:
        raise BonusServiceError("Guest profile does not belong to the selected venue.")
    guest = GuestProfile.objects.select_for_update().get(
        id=guest.id,
        partner_id=partner_id,
    )
    assert_bonus_balance_reconciled(guest)
    if order is not None:
        order = Order.objects.get(
            id=order.id,
            partner_id=partner_id,
            guest_id=guest.id,
        )
    if bill is not None and bill.partner_id != partner_id:
        raise BonusServiceError("Bill does not belong to the selected venue.")
    if walk_in_sale is not None and walk_in_sale.partner_id != partner_id:
        raise BonusServiceError("Walk-in sale does not belong to the selected venue.")

    transactions: list[BonusTransaction] = []
    context = BonusContext(
        event=event,
        guest=guest,
        order=order,
        purchase_total=purchase_total,
        comment=comment,
    )
    total_awarded = Decimal("0.00")
    programs = BonusProgramRepository.active_for_partner_event(partner_id, event)
    source_prefix = f"{source_type}:{source_id}" if source_type and source_id else ""

    for program in programs:
        idempotency_key = (
            f"{source_prefix}:program:{program.id}" if source_prefix else ""
        )
        if idempotency_key and BonusTransaction.objects.filter(
            partner_id=partner_id,
            idempotency_key=idempotency_key,
        ).exists():
            continue
        strategy = resolve_bonus_strategy(program)
        amount = Decimal(strategy(program, context)).quantize(Decimal("0.01"))
        if amount <= 0:
            continue

        expires_at = None
        if program.expires_in_days > 0:
            expires_at = timezone.now() + timedelta(days=program.expires_in_days)

        bonus_transaction = BonusTransaction.objects.create(
            partner_id=partner_id,
            guest=guest,
            program=program,
            order=order,
            bill=bill,
            walk_in_sale=walk_in_sale,
            transaction_type=BonusTransaction.TransactionType.ACCRUAL,
            amount=amount,
            expires_at=expires_at,
            source_type=source_type,
            source_id=str(source_id),
            idempotency_key=idempotency_key,
            comment=comment or f"Auto accrual via `{program.name}`.",
        )
        transactions.append(bonus_transaction)
        total_awarded += amount

    if total_awarded > 0:
        sync_bonus_balance_from_ledger(guest)

    return transactions


def apply_manual_purchase_bonus(
    *,
    partner_id,
    customer_code: str,
    purchase_total: Decimal | str,
    comment: str = "",
) -> list[BonusTransaction]:
    guest = get_guest_profile_by_customer_code(partner_id, customer_code)
    total = _parse_positive_money(purchase_total, label="Сумма покупки")
    return apply_bonus_programs(
        partner_id=partner_id,
        event=BonusProgram.TriggerEvent.MANUAL_PURCHASE,
        guest=guest,
        purchase_total=total,
        comment=comment or f"Manual purchase bonus for customer code {guest.customer_code}.",
    )


@transaction.atomic
def register_walk_in_sale(
    *,
    partner_id,
    guest: GuestProfile | None = None,
    customer_code_snapshot: str = "",
    amount: Decimal | str,
    comment: str = "",
    created_by=None,
    redeem_bonus: bool = False,
) -> WalkInSale:
    total = _parse_positive_money(amount, label="Сумма быстрой продажи")
    if guest is not None and guest.partner_id != partner_id:
        raise BonusServiceError("Guest profile does not belong to the selected venue.")
    if guest is not None:
        guest = GuestProfile.objects.select_for_update().get(
            id=guest.id,
            partner_id=partner_id,
        )
        assert_bonus_balance_reconciled(guest)
    sale = WalkInSale.objects.create(
        partner_id=partner_id,
        guest=guest,
        customer_code_snapshot=customer_code_snapshot or (guest.customer_code if guest else ""),
        amount=total,
        comment=comment,
        created_by=created_by,
    )

    # Redemption uses the balance the guest walked in with (before this sale's
    # accrual) and is capped by program max_redeem_share and the sale total.
    bonus_spent = Decimal("0.00")
    if guest is not None and redeem_bonus:
        bonus_spent = get_redeemable_bonus_amount(
            partner_id=partner_id,
            guest=guest,
            purchase_total=total,
        )
        if bonus_spent > 0:
            BonusTransaction.objects.create(
                partner_id=partner_id,
                guest=guest,
                program=None,
                order=None,
                walk_in_sale=sale,
                transaction_type=BonusTransaction.TransactionType.REDEMPTION,
                amount=bonus_spent,
                source_type="walk_in_sale",
                source_id=str(sale.id),
                idempotency_key=f"walk_in_sale:{sale.id}:redemption",
                comment=(
                    comment
                    or f"Walk-in sale redemption for customer code {guest.customer_code}."
                ),
            )
            sync_bonus_balance_from_ledger(guest)

    transactions = []
    if guest is not None:
        transactions = apply_bonus_programs(
            partner_id=partner_id,
            event=BonusProgram.TriggerEvent.MANUAL_PURCHASE,
            guest=guest,
            walk_in_sale=sale,
            purchase_total=total,
            source_type="walk_in_sale",
            source_id=str(sale.id),
            comment=comment or f"Walk-in sale bonus for customer code {guest.customer_code}.",
        )
    sale.bonus_awarded_amount = sum(
        (transaction.amount for transaction in transactions),
        Decimal("0.00"),
    )
    sale.bonus_spent_amount = bonus_spent
    sale.save(update_fields=["bonus_awarded_amount", "bonus_spent_amount", "updated_at"])
    return sale


def register_walk_in_sale_by_customer_code(
    *,
    partner_id,
    customer_code: str = "",
    amount: Decimal | str,
    comment: str = "",
    created_by=None,
) -> WalkInSale:
    # Empty code means an anonymous walk-in sale: the venue still tracks the sale
    # in the day report, but no guest is attached and no loyalty bonus is awarded.
    normalized_customer_code = customer_code.strip().upper()
    guest = None
    if normalized_customer_code:
        try:
            guest = get_guest_profile_by_customer_code(partner_id, normalized_customer_code)
        except GuestProfile.DoesNotExist as exc:
            raise BonusServiceError("Customer code was not found for this venue.") from exc
    return register_walk_in_sale(
        partner_id=partner_id,
        guest=guest,
        customer_code_snapshot=normalized_customer_code,
        amount=amount,
        comment=comment,
        created_by=created_by,
    )


def preview_walk_in_sale_by_customer_code(
    *,
    partner_id,
    customer_code: str,
    items: list[dict],
) -> WalkInSalePreview:
    # Empty code is allowed: this is an anonymous walk-in sale with no loyalty guest.
    normalized_customer_code = customer_code.strip().upper()
    guest = None
    if normalized_customer_code:
        try:
            guest = get_guest_profile_by_customer_code(partner_id, normalized_customer_code)
        except GuestProfile.DoesNotExist as exc:
            raise BonusServiceError("Customer code was not found for this venue.") from exc
    normalized_items: list[dict] = []
    total_amount = Decimal("0.00")
    projected_bonus_amount = Decimal("0.00")
    if items:
        normalized_items = _normalize_walk_in_sale_items(
            partner_id=partner_id,
            items=items,
        )
        total_amount = sum(
            (item["unit_price"] * item["quantity"] for item in normalized_items),
            Decimal("0.00"),
        )
        if guest is not None:
            projected_bonus_amount = _preview_bonus_amount(
                partner_id=partner_id,
                guest=guest,
                purchase_total=total_amount,
            )
    redeemable_bonus_amount = Decimal("0.00")
    if guest is not None and total_amount > 0:
        redeemable_bonus_amount = get_redeemable_bonus_amount(
            partner_id=partner_id,
            guest=guest,
            purchase_total=total_amount,
        )
    return WalkInSalePreview(
        guest=guest,
        items=normalized_items,
        total_amount=total_amount,
        projected_bonus_amount=projected_bonus_amount,
        redeemable_bonus_amount=redeemable_bonus_amount,
    )


@transaction.atomic
def register_walk_in_sale_from_menu_items_by_customer_code(
    *,
    partner_id,
    customer_code: str,
    items: list[dict],
    comment: str = "",
    created_by=None,
    redeem_bonus: bool = False,
) -> WalkInSale:
    preview = preview_walk_in_sale_by_customer_code(
        partner_id=partner_id,
        customer_code=customer_code,
        items=items,
    )
    sale = register_walk_in_sale(
        partner_id=partner_id,
        guest=preview.guest,
        customer_code_snapshot=preview.guest.customer_code if preview.guest else "",
        amount=preview.total_amount,
        comment=comment,
        created_by=created_by,
        redeem_bonus=redeem_bonus,
    )
    WalkInSaleItem.objects.bulk_create(
        [
            WalkInSaleItem(
                partner_id=partner_id,
                sale=sale,
                menu_item_id=item["menu_item_id"],
                item_name=item["item_name"],
                unit_price=item["unit_price"],
                quantity=item["quantity"],
            )
            for item in preview.items
        ]
    )
    return sale


def _preview_bonus_amount(
    *,
    partner_id,
    guest: GuestProfile,
    purchase_total: Decimal,
) -> Decimal:
    context = BonusContext(
        event=BonusProgram.TriggerEvent.MANUAL_PURCHASE,
        guest=guest,
        purchase_total=purchase_total,
    )
    total_awarded = Decimal("0.00")
    for program in BonusProgramRepository.active_for_partner_event(
        partner_id,
        BonusProgram.TriggerEvent.MANUAL_PURCHASE,
    ):
        strategy = resolve_bonus_strategy(program)
        amount = Decimal(strategy(program, context)).quantize(Decimal("0.01"))
        if amount > 0:
            total_awarded += amount
    return total_awarded


def get_redeemable_bonus_amount(
    *,
    partner_id,
    guest: GuestProfile,
    purchase_total: Decimal | str,
) -> Decimal:
    total = _parse_positive_money(purchase_total, label="Сумма покупки")
    if total <= 0:
        return Decimal("0.00")

    programs = BonusProgramRepository.active_for_partner(partner_id)
    max_redeem_share = Decimal("0.00")
    for program in programs:
        if program.max_redeem_share > max_redeem_share:
            max_redeem_share = Decimal(program.max_redeem_share)

    if max_redeem_share <= 0:
        return Decimal("0.00")

    share_cap = (total * max_redeem_share / Decimal("100")).quantize(Decimal("0.01"))
    return min(
        Decimal(guest.loyalty_balance).quantize(Decimal("0.01")),
        share_cap,
        total,
    ).quantize(Decimal("0.01"))


def _normalize_walk_in_sale_items(*, partner_id, items: list[dict]) -> list[dict]:
    if not items:
        raise BonusServiceError("At least one item is required for a quick sale.")

    menu_item_ids = [str(item["menu_item_id"]) for item in items]
    menu_items = {
        str(menu_item.id): menu_item
        for menu_item in MenuItem.objects.filter(
            partner_id=partner_id,
            id__in=menu_item_ids,
            is_available=True,
        )
    }
    normalized_items: list[dict] = []
    missing_ids = [menu_item_id for menu_item_id in menu_item_ids if menu_item_id not in menu_items]
    if missing_ids:
        raise BonusServiceError("Some quick-sale items are unavailable for this venue.")

    for item in items:
        menu_item = menu_items[str(item["menu_item_id"])]
        quantity = int(item.get("quantity", 0))
        if quantity <= 0:
            raise BonusServiceError("Quick-sale item quantity must be greater than zero.")
        normalized_items.append(
            {
                "menu_item_id": menu_item.id,
                "item_name": menu_item.name,
                "unit_price": Decimal(menu_item.price).quantize(Decimal("0.01")),
                "quantity": quantity,
            }
        )
    return normalized_items
