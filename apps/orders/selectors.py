from apps.orders.models import Order


def get_open_orders_for_partner(partner_id):
    return (
        Order.objects.filter(
            partner_id=partner_id,
        )
        .exclude(status=Order.Status.CANCELED)
        .exclude(status=Order.Status.COMPLETED, paid_at__isnull=False)
        .select_related(
            "partner",
            "table",
            "guest",
            "guest__telegram_account",
            "assigned_employee__user",
        )
        .prefetch_related("items")
        .order_by("created_at")
    )


def get_orders_for_session(session_id, *, partner_id):
    return (
        Order.objects.filter(partner_id=partner_id, table_session_id=session_id)
        .exclude(status=Order.Status.CANCELED)
        .select_related(
            "partner",
            "table",
            "guest",
            "guest__telegram_account",
            "assigned_employee__user",
        )
        .prefetch_related("items")
        .order_by("created_at")
    )
