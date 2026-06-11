from apps.orders.models import Cart, Order


def get_order_with_items(order_id):
    return Order.objects.select_related("guest", "table").prefetch_related("items").get(id=order_id)


def get_cart_with_items(cart_id):
    return (
        Cart.objects.select_related("guest", "table_session__table")
        .prefetch_related("items")
        .get(id=cart_id)
    )


def get_open_orders_for_partner(partner_id):
    return (
        Order.objects.filter(
            partner_id=partner_id,
        )
        .exclude(status=Order.Status.CANCELED)
        .exclude(status=Order.Status.COMPLETED, paid_at__isnull=False)
        .select_related(
            "table",
            "guest",
            "guest__telegram_account",
            "assigned_employee__user",
        )
        .prefetch_related("items")
        .order_by("created_at")
    )


def get_orders_for_session(session_id):
    return (
        Order.objects.filter(table_session_id=session_id)
        .exclude(status=Order.Status.CANCELED)
        .select_related(
            "table",
            "guest",
            "guest__telegram_account",
            "assigned_employee__user",
        )
        .prefetch_related("items")
        .order_by("created_at")
    )
