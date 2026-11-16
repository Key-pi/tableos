from celery import shared_task

from apps.tables.reservations import remind_due_reservations
from apps.tables.services import expire_idle_table_sessions

EXPIRE_IDLE_TABLE_SESSIONS_TASK = "tables.expire_idle_table_sessions"
REMIND_RESERVATIONS_TASK = "tables.remind_reservations"


@shared_task(name=EXPIRE_IDLE_TABLE_SESSIONS_TASK)
def expire_idle_table_sessions_task() -> int:
    """Close table sessions nobody used for the venue's window (PQ-008)."""

    return expire_idle_table_sessions()


@shared_task(name=REMIND_RESERVATIONS_TASK)
def remind_reservations_task() -> int:
    """Remind guests with the bot of their booking, once each (PQ-056)."""

    return remind_due_reservations()
