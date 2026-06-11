from apps.tables.models import Table, TableSession


class TableRepository:
    @staticmethod
    def available_for_partner(partner_id):
        return Table.objects.filter(partner_id=partner_id, is_active=True)


class TableSessionRepository:
    @staticmethod
    def active_for_guest(partner_id, guest_id):
        return TableSession.objects.filter(
            partner_id=partner_id,
            guest_id=guest_id,
            status=TableSession.Status.ACTIVE,
        ).select_related("table", "guest")
