from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.bonuses.repositories import BonusProgramRepository
from apps.bonuses.strategies import (
    BonusContext,
    BonusServiceError,
    resolve_bonus_strategy,
)
from apps.orders.models import Order
from apps.partners.models import NetworkWallet
from apps.partners.networks import shared_bonus_scope
from apps.users.models import GuestProfile
from apps.users.services import find_guest_by_customer_code
from core.i18n import _


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
            BonusTransaction.TransactionType.REVERSAL,
        }:
            balance += amount
        else:
            balance -= abs(amount)
    return balance.quantize(Decimal("0.01"))


def _network_ledger_balance(scope, telegram_account_id) -> Decimal:
    """A person's balance at every venue of a shared network: the sum of
    their venue ledgers (PQ-047, stage 3)."""

    balance = Decimal("0.00")
    transactions = BonusTransaction.objects.filter(
        partner_id__in=scope.partner_ids,
        guest__telegram_account_id=telegram_account_id,
    ).only("transaction_type", "amount")
    for bonus_transaction in transactions.iterator():
        amount = Decimal(bonus_transaction.amount).quantize(Decimal("0.01"))
        if bonus_transaction.transaction_type in {
            BonusTransaction.TransactionType.ACCRUAL,
            BonusTransaction.TransactionType.MANUAL,
            BonusTransaction.TransactionType.REVERSAL,
        }:
            balance += amount
        else:
            balance -= abs(amount)
    return balance.quantize(Decimal("0.01"))


def _locked_wallet(scope, telegram_account_id) -> NetworkWallet:
    """Lock the person's shared balance — after the venue's ``GuestProfile``
    (lock order: GuestProfile → NetworkWallet). A wallet made now starts
    with what the venues already earned: the balances add up."""

    lookup = {"network_id": scope.network_id, "telegram_account_id": telegram_account_id}
    wallet = NetworkWallet.objects.select_for_update().filter(**lookup).first()
    if wallet is None:
        try:
            with transaction.atomic():
                NetworkWallet.objects.create(
                    **lookup, balance=_network_ledger_balance(scope, telegram_account_id)
                )
        except IntegrityError:
            pass  # made by a parallel transaction: lock that one
        wallet = NetworkWallet.objects.select_for_update().get(**lookup)
    return wallet


def bonus_balance(guest: GuestProfile) -> Decimal:
    """What the guest may spend at this venue: the venue's balance, or the
    shared balance of the network (PQ-047)."""

    scope = shared_bonus_scope(guest.partner_id)
    if scope is None:
        return Decimal(guest.loyalty_balance).quantize(Decimal("0.01"))
    wallet = NetworkWallet.objects.filter(
        network_id=scope.network_id, telegram_account_id=guest.telegram_account_id
    ).first()
    if wallet is not None:
        return Decimal(wallet.balance).quantize(Decimal("0.01"))
    return _network_ledger_balance(scope, guest.telegram_account_id)


def assert_bonus_balance_reconciled(guest: GuestProfile) -> None:
    stored_balance = Decimal(guest.loyalty_balance).quantize(Decimal("0.01"))
    ledger_balance = get_bonus_ledger_balance(
        partner_id=guest.partner_id,
        guest_id=guest.id,
    )
    if stored_balance != ledger_balance:
        raise BonusServiceError(
            _("Баланс бонусов требует сверки с историей операций перед изменением.")
        )
    scope = shared_bonus_scope(guest.partner_id)
    if scope is not None:
        # Every bonus change comes here with the venue's guest locked: the
        # shared balance is locked next, so two venues take turns.
        wallet = _locked_wallet(scope, guest.telegram_account_id)
        if Decimal(wallet.balance).quantize(Decimal("0.01")) != _network_ledger_balance(
            scope, guest.telegram_account_id
        ):
            raise BonusServiceError(
                _("Баланс бонусов требует сверки с историей операций перед изменением.")
            )


def sync_bonus_balance_from_ledger(guest: GuestProfile) -> GuestProfile:
    ledger_balance = get_bonus_ledger_balance(
        partner_id=guest.partner_id,
        guest_id=guest.id,
    )
    if Decimal(guest.loyalty_balance).quantize(Decimal("0.01")) != ledger_balance:
        guest.loyalty_balance = ledger_balance
        guest.save(update_fields=["loyalty_balance", "updated_at"])
    scope = shared_bonus_scope(guest.partner_id)
    if scope is not None:
        wallet = _locked_wallet(scope, guest.telegram_account_id)
        network_balance = _network_ledger_balance(scope, guest.telegram_account_id)
        if Decimal(wallet.balance).quantize(Decimal("0.01")) != network_balance:
            wallet.balance = network_balance
            wallet.save(update_fields=["balance", "updated_at"])
    return guest


def _parse_positive_money(value: Decimal | str, *, label: str) -> Decimal:
    try:
        amount = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BonusServiceError(_(
            "{label} должна быть корректным денежным значением.",
            label=label,
        )) from exc
    if not amount.is_finite() or amount <= 0:
        raise BonusServiceError(_("{label} должна быть больше нуля.", label=label))
    return amount


@transaction.atomic
def apply_bonus_programs(
    *,
    partner_id,
    event: str,
    guest: GuestProfile,
    order: Order | None = None,
    bill=None,
    purchase_total: Decimal | None = None,
    bonus_paid_amount: Decimal | str = Decimal("0.00"),
    source_type: str = "",
    source_id: str = "",
    comment: str = "",
) -> list[BonusTransaction]:
    if guest.partner_id != partner_id:
        raise BonusServiceError(_("Гость не из этого заведения."))
    if guest.telegram_account_id is None:
        # The anonymous guest of a table visit earns nothing (PQ-052): bonuses
        # are for a guest named by code.
        return []
    bonus_paid_amount = Decimal(bonus_paid_amount).quantize(Decimal("0.01"))
    if not bonus_paid_amount.is_finite() or bonus_paid_amount < 0:
        raise BonusServiceError("Bonus-paid amount cannot be negative.")
    guest = GuestProfile.objects.select_for_update().get(
        id=guest.id,
        partner_id=partner_id,
    )
    assert_bonus_balance_reconciled(guest)
    if order is not None:
        # Not necessarily the guest's own order: the cashback is theirs who
        # pay the lines (PQ-009, step 3).
        order = Order.objects.get(id=order.id, partner_id=partner_id)
    if bill is not None and bill.partner_id != partner_id:
        raise BonusServiceError(_("Счёт не из этого заведения."))

    transactions: list[BonusTransaction] = []
    context = BonusContext(
        event=event,
        guest=guest,
        order=order,
        purchase_total=purchase_total,
        bonus_paid_amount=bonus_paid_amount,
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
    guest = find_guest_by_customer_code(partner_id, customer_code)
    total = _parse_positive_money(purchase_total, label=_("Сумма покупки"))
    return apply_bonus_programs(
        partner_id=partner_id,
        event=BonusProgram.TriggerEvent.MANUAL_PURCHASE,
        guest=guest,
        purchase_total=total,
        comment=comment or f"Manual purchase bonus for customer code {guest.customer_code}.",
    )


def preview_bonus_amount(
    *,
    partner_id,
    guest: GuestProfile,
    purchase_total: Decimal,
    bonus_paid_amount: Decimal = Decimal("0.00"),
    event: str = BonusProgram.TriggerEvent.MANUAL_PURCHASE,
) -> Decimal:
    """What the guest would earn for a purchase, before it is made (the
    till's preview). The anonymous guest earns nothing (PQ-052)."""

    if guest.telegram_account_id is None:
        return Decimal("0.00")
    context = BonusContext(
        event=event,
        guest=guest,
        purchase_total=purchase_total,
        bonus_paid_amount=bonus_paid_amount,
    )
    total_awarded = Decimal("0.00")
    for program in BonusProgramRepository.active_for_partner_event(partner_id, event):
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
    total = _parse_positive_money(purchase_total, label=_("Сумма покупки"))
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
        bonus_balance(guest),
        share_cap,
        total,
    ).quantize(Decimal("0.01"))
