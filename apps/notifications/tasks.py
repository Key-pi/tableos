from celery import shared_task

from apps.notifications.models import BroadcastCampaign, StaffNotification
from apps.notifications.services import (
    DELIVER_STAFF_NOTIFICATION_TASK,
    PROCESS_SCHEDULED_BROADCAST_CAMPAIGNS_TASK,
    RECOVER_STALLED_BROADCASTS_TASK,
    SEND_BROADCAST_CAMPAIGN_TASK,
    SEND_GUEST_ORDER_STATUS_UPDATE_TASK,
    deliver_staff_notification_now,
    process_scheduled_broadcast_campaigns,
    recover_stalled_broadcast_campaigns,
    send_broadcast_campaign_now,
    send_guest_order_status_update_now,
)


@shared_task(
    bind=True,
    name=DELIVER_STAFF_NOTIFICATION_TASK,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
    # Redeliver the task if the worker dies mid-run (at-least-once for
    # notifications; broadcast rows make a redelivered task harmless).
    acks_late=True,
    reject_on_worker_lost=True,
)
def deliver_staff_notification_task(self, notification_id: str) -> None:
    try:
        notification = StaffNotification.objects.get(id=notification_id)
    except StaffNotification.DoesNotExist:
        return
    deliver_staff_notification_now(notification)


@shared_task(
    bind=True,
    name=SEND_GUEST_ORDER_STATUS_UPDATE_TASK,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
    # Redeliver the task if the worker dies mid-run (at-least-once for
    # notifications; broadcast rows make a redelivered task harmless).
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_guest_order_status_update_task(
    self,
    order_id: str,
    from_status: str = "",
    to_status: str = "",
) -> None:
    send_guest_order_status_update_now(
        order_id=order_id,
        from_status=from_status,
        to_status=to_status,
    )


@shared_task(
    bind=True,
    name=SEND_BROADCAST_CAMPAIGN_TASK,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
    # Redeliver the task if the worker dies mid-run (at-least-once for
    # notifications; broadcast rows make a redelivered task harmless).
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_broadcast_campaign_task(self, campaign_id: str, after_guest_id: str = "") -> None:
    try:
        campaign = BroadcastCampaign.objects.get(id=campaign_id)
    except BroadcastCampaign.DoesNotExist:
        return
    send_broadcast_campaign_now(campaign, after_guest_id=after_guest_id)


@shared_task(
    bind=True,
    name=PROCESS_SCHEDULED_BROADCAST_CAMPAIGNS_TASK,
)
def process_scheduled_broadcast_campaigns_task(self) -> int:
    return process_scheduled_broadcast_campaigns()


@shared_task(
    bind=True,
    name=RECOVER_STALLED_BROADCASTS_TASK,
)
def recover_stalled_broadcast_campaigns_task(self) -> int:
    return recover_stalled_broadcast_campaigns()
