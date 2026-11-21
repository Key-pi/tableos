"""Background work of the Poster integration (PQ-058; docs/architecture/BACKGROUND_JOBS.md).

Every Poster request runs here, outside an open transaction (AGENTS.md).
"""

import logging

from celery import shared_task

from apps.integrations.poster import orders, statuses, webhooks
from apps.integrations.poster.menu_sync import PosterSyncError, sync_menu
from apps.integrations.poster.models import PosterConnection
from apps.partners.models import Partner

logger = logging.getLogger(__name__)

SYNC_MENU_TASK = "poster.sync_menu"
SYNC_ALL_MENUS_TASK = "poster.sync_all_menus"
PROCESS_WEBHOOK_TASK = "poster.process_webhook"
SYNC_MENU_AFTER_CHANGES_TASK = "poster.sync_menu_after_changes"
PUSH_ORDER_TASK = "poster.push_order"
RECOVER_ORDER_PUSHES_TASK = "poster.recover_order_pushes"
SYNC_ORDER_STATE_TASK = "poster.sync_order_state"


@shared_task(name=SYNC_MENU_TASK)
def sync_menu_task(connection_id: str) -> str:
    """One venue's menu from Poster (step 3). A refusal is remembered on
    the connection (``last_error``); the next run tries again."""

    try:
        return sync_menu(connection_id=connection_id).summary()
    except PosterSyncError as exc:
        logger.warning("Poster menu of connection %s not synced: %s", connection_id, exc)
        return ""


@shared_task(name=SYNC_ALL_MENUS_TASK)
def sync_all_menus_task() -> int:
    """At night: every working connection of a venue that is not suspended."""

    ids = list(
        PosterConnection.objects.filter(status=PosterConnection.Status.ACTIVE)
        .exclude(partner__status=Partner.Status.SUSPENDED)
        .values_list("id", flat=True)
    )
    for connection_id in ids:
        sync_menu_task.delay(str(connection_id))
    return len(ids)


@shared_task(name=PROCESS_WEBHOOK_TASK)
def process_webhook_task(event_id: str) -> str:
    """One webhook from the inbox (step 4); a repeat does nothing."""

    return webhooks.process(event_id)


@shared_task(name=SYNC_MENU_AFTER_CHANGES_TASK)
def sync_menu_after_changes_task(connection_id: str, stamp: str) -> str:
    """A minute after a menu webhook: the menu, unless Poster changed it
    again since — then the later task brings it."""

    if not webhooks.menu_quiet(connection_id, stamp):
        return ""
    return sync_menu_task(connection_id)


@shared_task(name=PUSH_ORDER_TASK)
def push_order_task(push_id: str) -> str:
    """One order to the Poster register (step 5); a repeat does nothing."""

    return orders.push_order(push_id)


@shared_task(name=RECOVER_ORDER_PUSHES_TASK)
def recover_order_pushes_task() -> int:
    """Orders to Poster that a lost task or a stopped worker left."""

    return orders.recover_pushes()


@shared_task(name=SYNC_ORDER_STATE_TASK)
def sync_order_state_task(push_id: str, attempt: int = 1) -> str:
    """What the register said about one Botera order (step 6); a repeat
    does nothing new."""

    return statuses.sync_order_state(push_id, attempt=attempt)
