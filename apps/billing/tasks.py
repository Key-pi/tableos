from celery import shared_task

from apps.billing.remote_orders import cancel_expired_transfer_orders

CANCEL_EXPIRED_TRANSFER_ORDERS_TASK = "billing.cancel_expired_transfer_orders"


@shared_task(name=CANCEL_EXPIRED_TRANSFER_ORDERS_TASK)
def cancel_expired_transfer_orders_task() -> int:
    """Cancel remote orders whose transfer nobody confirmed in time (PQ-036)."""

    return cancel_expired_transfer_orders()
