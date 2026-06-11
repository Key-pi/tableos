from apps.bonuses.models import BonusProgram
from apps.bonuses.repositories import BonusProgramRepository, BonusTransactionRepository


def default_bonus_program(partner_id):
    return BonusProgram.objects.filter(partner_id=partner_id, is_active=True).first()


def active_bonus_programs_for_partner(partner_id):
    return BonusProgramRepository.active_for_partner(partner_id)


def recent_bonus_transactions_for_guest(guest_id, *, limit: int = 5):
    return BonusTransactionRepository.for_guest(guest_id)[:limit]
