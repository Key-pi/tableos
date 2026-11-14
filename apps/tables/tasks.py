from celery import shared_task

from apps.tables.services import expire_idle_table_sessions

EXPIRE_IDLE_TABLE_SESSIONS_TASK = "tables.expire_idle_table_sessions"


@shared_task(name=EXPIRE_IDLE_TABLE_SESSIONS_TASK)
def expire_idle_table_sessions_task() -> int:
    """Close table sessions nobody used for the venue's window (PQ-008)."""

    return expire_idle_table_sessions()
