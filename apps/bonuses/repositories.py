from apps.bonuses.models import BonusProgram, BonusTransaction


class BonusProgramRepository:
    @staticmethod
    def active_for_partner(partner_id):
        return BonusProgram.objects.filter(partner_id=partner_id, is_active=True)

    @staticmethod
    def active_for_partner_event(partner_id, trigger_event: str):
        return BonusProgram.objects.filter(
            partner_id=partner_id,
            is_active=True,
            trigger_event=trigger_event,
        )


class BonusTransactionRepository:
    @staticmethod
    def for_guest(guest_id):
        return (
            BonusTransaction.objects.filter(guest_id=guest_id)
            .select_related("program")
            .order_by("-created_at")
        )
