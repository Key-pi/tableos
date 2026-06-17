"""Async tasks for materialized analytics snapshots."""

from celery import shared_task

from apps.analytics.services import refresh_daily_partner_metrics

REFRESH_DAILY_PARTNER_METRICS_TASK = "analytics.refresh_daily_partner_metrics"


@shared_task(
    bind=True,
    name=REFRESH_DAILY_PARTNER_METRICS_TASK,
)
def refresh_daily_partner_metrics_task(self) -> int:
    return refresh_daily_partner_metrics()
