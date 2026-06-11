from apps.billing.models import Bill, BillingRequest, BillOrder
from apps.orders.models import Order
from apps.tables.models import TableSession


class BillRepository:
    @staticmethod
    def open_for_partner(partner_id):
        return Bill.objects.filter(
            partner_id=partner_id,
            status__in=[
                Bill.Status.DRAFT,
                Bill.Status.ISSUED,
                Bill.Status.PARTIALLY_PAID,
            ],
        ).select_related(
            "table",
            "primary_guest",
            "primary_guest__telegram_account",
        ).prefetch_related(
            "bill_orders",
            "items",
            "payments",
            "billing_requests",
        )

    @staticmethod
    def by_public_id_for_partner(partner_id, public_id: str):
        return BillRepository.open_for_partner(partner_id).get(public_id=public_id)


class BillingRequestRepository:
    @staticmethod
    def open_for_partner(partner_id):
        return BillingRequest.objects.filter(
            partner_id=partner_id,
            status__in=[
                BillingRequest.Status.OPEN,
                BillingRequest.Status.AUTO_PREPARED,
            ],
        ).select_related(
            "table",
            "guest",
            "guest__telegram_account",
            "bill",
        ).order_by("-created_at")

    @staticmethod
    def by_id_for_partner(partner_id, request_id):
        return BillingRequestRepository.open_for_partner(partner_id).get(id=request_id)


class BillingOperationsRepository:
    @staticmethod
    def active_sessions_for_partner(partner_id):
        return TableSession.objects.filter(
            partner_id=partner_id,
            status=TableSession.Status.ACTIVE,
        ).select_related(
            "table",
            "guest",
            "guest__telegram_account",
        ).order_by("table__number", "-started_at")

    @staticmethod
    def unbilled_open_orders_for_partner(partner_id):
        allocated_order_ids = set(
            BillOrder.objects.filter(
                partner_id=partner_id,
                bill__status__in=[
                    Bill.Status.DRAFT,
                    Bill.Status.ISSUED,
                    Bill.Status.PARTIALLY_PAID,
                    Bill.Status.PAID,
                ],
            ).values_list("order_id", flat=True)
        )
        return Order.objects.filter(
            partner_id=partner_id,
            paid_at__isnull=True,
        ).exclude(
            status=Order.Status.CANCELED,
        ).exclude(
            id__in=allocated_order_ids,
        ).select_related(
            "table",
            "guest",
            "guest__telegram_account",
            "assigned_employee__user",
        ).prefetch_related("items").order_by("table__number", "created_at")
