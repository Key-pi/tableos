from apps.bonuses.models import BonusProgram
from apps.bonuses.repositories import BonusProgramRepository, BonusTransactionRepository


def default_bonus_program(partner_id):
    return BonusProgram.objects.filter(partner_id=partner_id, is_active=True).first()


def active_bonus_programs_for_partner(partner_id):
    return BonusProgramRepository.active_for_partner(partner_id)


def purchase_event_for(order) -> str:
    """Which programmes a paid order earns by: a sale at the counter
    (PQ-054) by «Покупка на кассе», as the quick sale did; other orders by
    «Оплаченный заказ»."""

    from apps.bonuses.models import BonusProgram

    if order.channel == "counter":
        return BonusProgram.TriggerEvent.MANUAL_PURCHASE
    return BonusProgram.TriggerEvent.ORDER_COMPLETED


def bonuses_work_at(partner_id) -> bool:
    """Whether the venue has a working bonus programme (PQ-001).

    Bonuses are part of the base, not a module: a venue that does not want
    them simply has no programme switched on, and then the guest is not shown
    the bonus code, the balance or the «Бонусы» button. In a network each
    venue decides for itself, as with spending (``get_redeemable_bonus_amount``).
    """

    return BonusProgramRepository.active_for_partner(partner_id).exists()


def recent_bonus_transactions_for_guest(guest_id, *, limit: int = 5):
    """The guest's latest bonus operations; with shared bonuses in a network
    (PQ-047) — at every venue of it, since they are one balance."""

    from apps.bonuses.models import BonusTransaction
    from apps.partners.networks import shared_bonus_scope
    from apps.users.models import GuestProfile

    guest = (
        GuestProfile.objects.filter(id=guest_id)
        .values("partner_id", "telegram_account_id")
        .first()
    )
    scope = shared_bonus_scope(guest["partner_id"]) if guest else None
    if scope is None:
        return BonusTransactionRepository.for_guest(guest_id)[:limit]
    return (
        BonusTransaction.objects.filter(
            partner_id__in=scope.partner_ids,
            guest__telegram_account_id=guest["telegram_account_id"],
        )
        .select_related("program", "partner")
        .order_by("-created_at")[:limit]
    )
