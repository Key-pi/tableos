"""Async tasks for materialized analytics snapshots."""

from celery import shared_task

from apps.analytics.services import refresh_daily_partner_metrics

REFRESH_DAILY_PARTNER_METRICS_TASK = "analytics.refresh_daily_partner_metrics"
RECORD_SYSTEM_SNAPSHOT_TASK = "analytics.record_system_snapshot"


@shared_task(
    bind=True,
    name=REFRESH_DAILY_PARTNER_METRICS_TASK,
)
def refresh_daily_partner_metrics_task(self) -> int:
    return refresh_daily_partner_metrics()


@shared_task(name=RECORD_SYSTEM_SNAPSHOT_TASK, ignore_result=True)
def record_system_snapshot_task() -> None:
    """The platform's load, once a minute (PQ-051). A late snapshot is itself
    the sign: the page warns when the worker or beat stopped."""

    from apps.analytics.system_load import take_snapshot

    take_snapshot()
