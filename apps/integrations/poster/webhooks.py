"""Poster webhooks (PQ-058, step 4; plan — docs/architecture/POSTER.md, «Вебхуки»).

``POST /integrations/poster/webhook/`` (``views.webhook``) answers at once;
the work is a Celery task.

1. ``signature_ok`` — md5 of ``account;object;object_id;action;[data;]time;secret``
   with ``data`` as it came (a JSON string), compared in constant time. A
   wrong signature, or no secret on the platform, writes nothing (403).
2. ``receive`` — one ``PosterWebhookEvent`` per event
   (``PosterWebhookEvent.dedupe_key_for``); Poster repeating an event, even
   at the same moment, is answered «accept» with no second row and no second
   task (the unique key settles a race). ``poster.process_webhook`` is queued
   after the commit.
3. ``process`` (the task) — the venues are the working connections of the
   signed ``account`` login; ``account_number`` is not signed and chooses
   nothing. A suspended venue is skipped. None left — the event is «не наш».
   A change of the menu (``product``, ``dish``, ``category``) marks each
   connection (``PosterConnection.menu_changed_at``) and puts off
   ``poster.sync_menu_after_changes`` by a minute: Poster sends a webhook per
   changed product, the menu is brought once, after the last one
   (``menu_quiet``). Orders and checks (``incoming_order``, ``transaction``)
   are kept for steps 5–6; other objects (``client``…) are not needed.

Locks: the ``PosterWebhookEvent`` row → the account's ``PosterConnection``
rows by id. An event is processed once: its status is checked under its lock.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from urllib.parse import parse_qsl

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.integrations.poster.models import PosterConnection, PosterWebhookEvent
from apps.partners.models import Partner
from core.i18n import _

MENU_OBJECTS = frozenset({"product", "dish", "category"})
ORDER_OBJECTS = frozenset({"incoming_order", "transaction"})
MENU_QUIET_SECONDS = 60
Status = PosterWebhookEvent.Status


# --- the request ---------------------------------------------------------------


def read_body(raw: bytes) -> dict | None:
    """Poster posts JSON; a form is read too (``application/x-www-form-urlencoded``)."""

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        body = json.loads(text)
    except ValueError:
        body = dict(parse_qsl(text, keep_blank_values=True))
    return body if isinstance(body, dict) and body else None


def _signed_data(data) -> str | None:
    if data is None:
        return None
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def signature(body: dict, secret: str) -> str:
    parts = [str(body.get(name, "")) for name in ("account", "object", "object_id", "action")]
    data = _signed_data(body.get("data"))
    if data is not None:
        parts.append(data)
    parts += [str(body.get("time", "")), secret]
    return hashlib.md5(";".join(parts).encode("utf-8")).hexdigest()


def signature_ok(body: dict, secret: str) -> bool:
    given = body.get("verify")
    if not secret or not isinstance(given, str) or not given:
        return False
    return hmac.compare_digest(signature(body, secret), given)


# --- the inbox --------------------------------------------------------------


def _clip(value, name: str) -> str:
    return str(value if value is not None else "")[: PosterWebhookEvent._meta.get_field(
        name).max_length]


def _unix_time(value) -> int:
    try:
        return max(int(str(value).strip()), 0)
    except (TypeError, ValueError):
        return 0


def _seen(key: str) -> bool:
    return PosterWebhookEvent.objects.filter(dedupe_key=key).exists()


def receive(body: dict) -> PosterWebhookEvent | None:
    """Keep a signed event; None when Poster repeats one already kept."""

    from apps.integrations.poster.tasks import process_webhook_task

    key = PosterWebhookEvent.dedupe_key_for(body)
    if _seen(key):
        return None
    try:
        with transaction.atomic():
            event = PosterWebhookEvent.objects.create(
                dedupe_key=key,
                account=_clip(body.get("account"), "account"),
                entity=_clip(body.get("object"), "entity"),
                entity_id=_clip(body.get("object_id"), "entity_id"),
                action=_clip(body.get("action"), "action"),
                event_time=_unix_time(body.get("time")),
                payload={name: value for name, value in body.items() if name != "verify"},
            )
            transaction.on_commit(lambda: process_webhook_task.delay(str(event.pk)))
    except IntegrityError:
        return None  # the same event, written a moment ago by Poster's repeat
    return event


# --- processing ---------------------------------------------------------------


def _finish(event: PosterWebhookEvent, status: str, note: str = "") -> str:
    event.status = status
    event.error = note
    event.processed_at = timezone.now()
    event.save(update_fields=["partner", "status", "error", "processed_at", "updated_at"])
    return status


def _connections_of(event: PosterWebhookEvent) -> list[PosterConnection]:
    """The working connections of the signed login, locked by id."""

    return list(
        PosterConnection.objects.select_for_update(of=("self",))
        .filter(account=event.account, status=PosterConnection.Status.ACTIVE)
        .exclude(partner__status=Partner.Status.SUSPENDED)
        .order_by("pk")
    )


def process(event_id) -> str:
    """Do what the event asks; "" when it was processed already."""

    from apps.integrations.poster.tasks import sync_menu_after_changes_task

    with transaction.atomic():
        event = PosterWebhookEvent.objects.select_for_update().filter(pk=event_id).first()
        if event is None or event.status != Status.RECEIVED:
            return ""
        connections = _connections_of(event)
        if not connections:
            return _finish(event, Status.IGNORED, _(
                "Нет работающего подключения к этому аккаунту Poster."
            ))
        # One account may serve several venues (its spots); then no single one.
        event.partner_id = connections[0].partner_id if len(connections) == 1 else None
        if event.entity in ORDER_OBJECTS:
            return _finish(event, Status.IGNORED, _(
                "Заказы и чеки из Poster Botera начнёт разбирать на шагах 5–6."
            ))
        if event.entity not in MENU_OBJECTS:
            return _finish(event, Status.IGNORED, _("Этот вебхук Botera не нужен."))
        changed_at = timezone.now()
        for connection in connections:
            connection.menu_changed_at = changed_at
            connection.save(update_fields=["menu_changed_at", "updated_at"])
        later = [(str(connection.pk), changed_at.isoformat()) for connection in connections]
        transaction.on_commit(lambda: [
            sync_menu_after_changes_task.apply_async(args, countdown=MENU_QUIET_SECONDS)
            for args in later
        ])
        return _finish(event, Status.PROCESSED)


def menu_quiet(connection_id, stamp: str) -> bool:
    """Nothing changed in Poster since the change of ``stamp``."""

    try:
        changed_at = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return False
    last = (
        PosterConnection.objects.filter(pk=connection_id)
        .values_list("menu_changed_at", flat=True).first()
    )
    return last is not None and last == changed_at
