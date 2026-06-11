from apps.tables.models import Table


def get_table_by_qr_token(qr_token: str) -> Table:
    return Table.objects.select_related("partner").get(qr_token=qr_token, is_active=True)
