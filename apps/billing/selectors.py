from apps.billing.models import Bill


def table_bills(partner_id, table_id):
    return Bill.objects.filter(
        partner_id=partner_id,
        table_id=table_id,
    ).select_related("primary_guest").order_by("-created_at")

