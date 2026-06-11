from apps.orders.models import Cart, Order


class OrderRepository:
    @staticmethod
    def open_for_partner(partner_id):
        return Order.objects.filter(
            partner_id=partner_id,
        ).exclude(
            status=Order.Status.CANCELED,
        ).exclude(
            status=Order.Status.COMPLETED,
            paid_at__isnull=False,
        ).select_related(
            "table",
            "guest",
            "guest__telegram_account",
            "table_session",
            "assigned_employee__user",
        )

    @staticmethod
    def by_public_id_for_partner(partner_id, public_id: str):
        return (
            Order.objects.select_related(
                "table",
                "guest",
                "guest__telegram_account",
                "table_session",
                "assigned_employee__user",
            )
            .prefetch_related("items")
            .get(
                partner_id=partner_id,
                public_id=public_id,
            )
        )


class CartRepository:
    @staticmethod
    def active_for_partner(partner_id):
        return Cart.objects.filter(
            partner_id=partner_id,
            status=Cart.Status.ACTIVE,
        ).select_related("guest", "table_session", "table_session__table")
