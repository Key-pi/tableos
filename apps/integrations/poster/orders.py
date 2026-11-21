"""Orders from the bot to the Poster register (PQ-058, step 5; plan —
docs/architecture/POSTER.md, «Заказ на кассу»).

Once a remote order is committed (``apps/billing/signals.py``),
``queue_order`` writes a ``PosterOrderPush`` and queues ``poster.push_order``
— for pickup and delivery of a venue whose Poster works. Billing knows
nothing of Poster, and nothing here can fail an order: it is saved in
Botera first.

``push_order`` (the task) works as the broadcasts of ADR-0008:

1. claims the row in a short transaction (``pending`` / ``unknown`` →
   ``sending``, one more attempt) — one worker sends it;
2. outside any transaction, an attempt after an unclear one first looks for
   the online order Poster may have made: the inbox's ``incoming_order``
   webhooks of the account (``incomingOrders.getOwnIncomingOrders`` is
   closed to applications, step 0) are read with
   ``incomingOrders.getIncomingOrder`` and matched by the label
   ``Botera #<code>`` and the spot. Found — ``sent``, no second order;
3. sends ``incomingOrders.createIncomingOrder``: the guest's phone and name,
   «З собою» (``service_mode`` 2) or delivery (3) with the address,
   ``delivery_price`` in kopecks (Poster adds it to the check) and
   ``delivery_time`` in the venue's time; lines by Poster ids — a variant as
   ``modificator_id``, add-ons as ``modification`` ``[{"m": id, "a": 1}]``; the
   comment says the way and time, the guest's comment and the label. No
   payment: v1 is paid on receipt;
4. writes the result in its own transaction: ``sent`` with the online order
   and its check; unclear (network, timeout, Poster busy) — ``unknown`` and
   another attempt later (``RETRY_DELAYS``), ``MAX_ATTEMPTS`` at most; a
   refusal — ``failed``.

What v1 does not send — paid in advance, paid with bonuses, a line, variant
or add-on not from Poster — and every ``failed`` go to the staff who hear
about this way of ordering, even with new-order messages muted: take the
order by hand (``notify_staff_about_order_problem``). The order in Botera
stays as it is.

``recover_pushes`` (beat) takes again what a lost task or a stopped worker
left. Lock: only the ``PosterOrderPush`` row.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.integrations.poster.client import PosterClient, PosterError
from apps.integrations.poster.models import (
    PosterConnection,
    PosterOrderPush,
    PosterRef,
    PosterWebhookEvent,
)
from apps.notifications.services import notify_staff_about_order_problem
from apps.orders.models import Order, OrderItem
from apps.orders.services import requested_time_label
from apps.partners.languages import main_language
from apps.partners.models import PartnerBotSettings
from core.i18n import N_, _, gettext_in, texts

logger = logging.getLogger(__name__)

Push = PosterOrderPush.Status
Kind = PosterRef.Kind
# The ways of ordering v1 sends, as Poster's ``service_mode``.
SERVICE_MODES = {Order.Channel.PICKUP: "2", Order.Channel.DELIVERY: "3"}
MAX_ATTEMPTS = 5
RETRY_DELAYS = (60, 120, 300, 600)  # seconds before attempts 2, 3, 4, 5
STALE_PENDING = timedelta(minutes=2)
STALE_SENDING = timedelta(minutes=10)
STALE_UNKNOWN = timedelta(minutes=15)
# Online orders made a little before the first attempt count as candidates.
LOOKUP_BEFORE = timedelta(minutes=2)
LOOKUP_LIMIT = 20
MIN_PHONE_DIGITS = 9


@dataclass(frozen=True)
class Reason:
    """Why an order is not in Poster, translated where it is read."""

    text: str
    params: dict = field(default_factory=dict)
    # True when Poster may have made the online order all the same.
    maybe_sent: bool = False

    def __str__(self) -> str:
        return _(self.text, **self.params)


class PushRefused(Exception):
    def __init__(self, reason: Reason) -> None:
        super().__init__(reason.text)
        self.reason = reason


def _refuse(text: str, **params) -> PushRefused:
    return PushRefused(Reason(text, params))


def label(order: Order) -> str:
    """How Botera finds its order in Poster: in the online order's comment."""

    return f"Botera #{order.public_id}"


# --- queueing ---------------------------------------------------------------


def queue_order(order_id) -> PosterOrderPush | None:
    """A push for a committed order of a venue on Poster; None otherwise or
    when it is queued already."""

    from apps.integrations.poster.tasks import push_order_task

    order = Order.objects.filter(pk=order_id).only("id", "partner_id", "channel").first()
    if order is None or order.channel not in SERVICE_MODES:
        return None
    if not PosterConnection.objects.filter(
        partner_id=order.partner_id, status=PosterConnection.Status.ACTIVE,
    ).exists():
        return None
    with transaction.atomic():
        push, created = PosterOrderPush.objects.get_or_create(
            order_id=order.pk, defaults={"partner_id": order.partner_id},
        )
        if not created:
            return None
        transaction.on_commit(lambda: push_order_task.delay(str(push.pk)))
    return push


def on_remote_order_placed(sender, order_id, **kwargs) -> None:
    """``apps/billing/signals.py:remote_order_placed``, connected in ``apps.py``."""

    queue_order(order_id)


# --- what is sent -------------------------------------------------------------


def _phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    return f"+{digits}" if len(digits) >= MIN_PHONE_DIGITS else ""


def _guest_name(order: Order) -> str:
    account = order.guest.telegram_account
    name = " ".join(
        part for part in (getattr(account, "first_name", ""), getattr(account, "last_name", ""))
        if part
    )
    return name or _("Гость Botera")


def _comment(order: Order) -> str:
    when = requested_time_label(order)
    if order.channel == Order.Channel.DELIVERY:
        parts = [_("Доставка {when}", when=when)]
    else:
        parts = [_("Самовывоз {when}", when=when)]
    if order.comment.strip():
        parts.append(order.comment.strip())
    parts.append(label(order))
    return "\n".join(parts)


def _as_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _poster_id(value: str):
    return int(value) if value.isdigit() else value


def _lines(order: Order, lines: list[OrderItem]) -> tuple[list[dict], Decimal]:
    """Poster's products of the order and the delivery fee (not a product)."""

    refs = {
        (ref.kind, ref.object_id): ref
        for ref in PosterRef.objects.filter(
            partner_id=order.partner_id, kind__in=(Kind.PRODUCT, Kind.VARIANT, Kind.MODIFIER),
        )
    }
    products, fee = [], Decimal("0.00")
    for line in lines:
        if line.kind == OrderItem.Kind.DELIVERY_FEE:
            fee += line.unit_price * line.quantity
            continue
        product = refs.get((Kind.PRODUCT, line.menu_item_id))
        if product is None:
            raise _refuse(N_("«{name}» нет в меню Poster"), name=line.item_name)
        entry = {"product_id": product.poster_id, "count": line.quantity}
        if line.variant_id:
            variant = refs.get((Kind.VARIANT, line.variant_id))
            product_id, _sep, modificator_id = (variant.poster_id if variant else "").partition(":")
            if product_id != product.poster_id or not modificator_id:
                raise _refuse(N_("вариант «{name}» не из Poster"), name=line.item_name)
            entry["modificator_id"] = modificator_id
        modifications = []
        for row in line.modifiers or []:
            modifier = refs.get((Kind.MODIFIER, _as_uuid(row.get("id"))))
            _group, _sep, modification_id = (modifier.poster_id if modifier else "").partition(":")
            if not modification_id:
                raise _refuse(N_("добавка «{name}» не из Poster"),
                              name=row.get("name") or line.item_name)
            modifications.append({"m": _poster_id(modification_id), "a": 1})
        if modifications:
            entry["modification"] = json.dumps(modifications)  # a JSON string, as Poster takes
        products.append(entry)
    if not products:
        raise _refuse(N_("в заказе нет позиций"))
    return products, fee


def build_order(order: Order, connection: PosterConnection, language: str) -> dict:
    """The body of ``incomingOrders.createIncomingOrder``; ``PushRefused``
    for what v1 does not send."""

    if order.channel not in SERVICE_MODES:
        raise _refuse(N_("этот способ заказа в Poster не передаётся"))
    if order.payment_timing != Order.PaymentTiming.ON_RECEIPT:
        raise _refuse(N_(
            "заказ оплачен переводом заранее, а в Poster передаются только заказы с оплатой при "
            "получении"
        ))
    if order.bonus_spent > 0:
        raise _refuse(N_("часть заказа оплачена бонусами Botera — такие заказы в Poster пока не "
                         "передаются"))
    phone = _phone(order.guest.phone)
    if not phone:
        raise _refuse(N_("у гостя нет телефона"))
    products, fee = _lines(order, list(order.items.all()))
    with texts(language):
        body = {
            "spot_id": str(connection.spot_id),
            "phone": phone,
            "first_name": _guest_name(order),
            "service_mode": SERVICE_MODES[order.channel],
            "comment": _comment(order),
            "products": products,
        }
    if order.channel == Order.Channel.DELIVERY:
        body["address"] = order.delivery_address
        if fee > 0:
            body["delivery_price"] = int((fee * 100).to_integral_value())
        if order.requested_for is not None:
            local = order.requested_for.astimezone(ZoneInfo(order.partner.timezone))
            body["delivery_time"] = local.strftime("%Y-%m-%d %H:%M:%S")
    return body


# --- sending ------------------------------------------------------------------


def _claimable(push: PosterOrderPush) -> bool:
    return push.status in (Push.PENDING, Push.UNKNOWN)


def _claim(push_id) -> tuple[PosterOrderPush | None, str]:
    with transaction.atomic():
        push = PosterOrderPush.objects.select_for_update().filter(pk=push_id).first()
        if push is None or not _claimable(push):
            return None, ""
        previous = push.status
        push.status = Push.SENDING
        push.attempts += 1
        push.claimed_at = timezone.now()
        push.save(update_fields=["status", "attempts", "claimed_at", "updated_at"])
    return push, previous


def _find_sent(client, connection: PosterConnection, order: Order,
               push: PosterOrderPush) -> dict | None:
    """The online order Poster made for this order, if any (see 2. above)."""

    taken = {
        str(number) for number in PosterOrderPush.objects.filter(
            partner_id__in=PosterConnection.objects.filter(account=connection.account)
            .values("partner_id"),
            incoming_order_id__isnull=False,
        ).exclude(pk=push.pk).values_list("incoming_order_id", flat=True)
    }
    candidates = (
        PosterWebhookEvent.objects.filter(
            account=connection.account, entity="incoming_order", action="added",
            created_at__gte=push.created_at - LOOKUP_BEFORE,
        ).order_by("-created_at").values_list("entity_id", flat=True)
    )
    looked: set = set()
    for entity_id in candidates:
        if entity_id in looked or entity_id in taken or not entity_id.isdigit():
            continue
        if len(looked) >= LOOKUP_LIMIT:
            break
        looked.add(entity_id)
        try:
            answer = client.call("incomingOrders.getIncomingOrder",
                                 params={"incoming_order_id": entity_id})
        except PosterError as exc:
            if exc.temporary:
                raise
            continue
        if (
            isinstance(answer, dict)
            and label(order) in str(answer.get("comment") or "")
            and str(answer.get("spot_id")) == str(connection.spot_id)
        ):
            return answer
    return None


def _refusal(exc: PosterError) -> Reason:
    if exc.code == 155:
        return Reason(N_("Poster не принял телефон гостя ({reason})"), {"reason": exc.message})
    if exc.code == 210:
        return Reason(N_("в Poster у позиции не выбрана обязательная добавка ({reason})"),
                      {"reason": exc.message})
    return Reason(N_("Poster отказал: {reason}"), {"reason": exc.message})


def push_order(push_id) -> str:
    """Send one order to Poster; the push's new status, "" when another
    worker has it or it is done."""

    push, previous = _claim(push_id)
    if push is None:
        return ""
    order = Order.objects.select_related("partner", "guest__telegram_account").get(
        pk=push.order_id,
    )
    connection = PosterConnection.objects.filter(
        partner_id=order.partner_id, status=PosterConnection.Status.ACTIVE,
    ).first()
    token = connection.get_access_token() if connection is not None else ""
    if not token or not connection.spot_id:
        return _fail(push.pk, order, Reason(N_("Poster отключён — заказ не передан")))
    settings = PartnerBotSettings.objects.filter(partner_id=order.partner_id).first()
    language = main_language(settings)

    # Network only from here, outside any transaction.
    with PosterClient(token) as client:
        try:
            if previous == Push.UNKNOWN:
                found = _find_sent(client, connection, order, push)
                if found is not None:
                    return _sent(push.pk, found)
            try:
                body = build_order(order, connection, language)
            except PushRefused as exc:
                return _fail(push.pk, order, exc.reason)
            answer = client.call("incomingOrders.createIncomingOrder", body=body)
        except PosterError as exc:
            if exc.temporary:
                return _unclear(push.pk, order, exc.message)
            return _fail(push.pk, order, _refusal(exc))
    if not isinstance(answer, dict) or not answer.get("incoming_order_id"):
        return _unclear(push.pk, order, "Poster answered without incoming_order_id")
    return _sent(push.pk, answer)


# --- the result -------------------------------------------------------------------


def _locked(push_id) -> PosterOrderPush:
    return PosterOrderPush.objects.select_for_update().get(pk=push_id)


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sent(push_id, answer: dict) -> str:
    with transaction.atomic():
        push = _locked(push_id)
        push.status = Push.SENT
        push.sent_at = timezone.now()
        push.incoming_order_id = _number(answer.get("incoming_order_id"))
        push.transaction_id = _number(answer.get("transaction_id"))
        push.last_error = ""
        push.save(update_fields=[
            "status", "sent_at", "incoming_order_id", "transaction_id", "last_error",
            "updated_at",
        ])
    return Push.SENT


def _retry_later(push_id: str, countdown: int) -> None:
    from apps.integrations.poster.tasks import push_order_task

    push_order_task.apply_async((push_id,), countdown=countdown)


def _unclear(push_id, order: Order, message: str) -> str:
    with transaction.atomic():
        push = _locked(push_id)
        if push.status != Push.SENDING:
            return ""
        if push.attempts >= MAX_ATTEMPTS:
            return _fail_locked(push, order, Reason(
                N_("Poster не ответил после {count} попыток ({reason})"),
                {"count": push.attempts, "reason": message}, maybe_sent=True,
            ))
        push.status = Push.UNKNOWN
        push.last_error = message
        push.save(update_fields=["status", "last_error", "updated_at"])
        countdown = RETRY_DELAYS[min(push.attempts, len(RETRY_DELAYS)) - 1]
        push_key = str(push.pk)
        transaction.on_commit(lambda: _retry_later(push_key, countdown))
    return Push.UNKNOWN


def _fail(push_id, order: Order, reason: Reason) -> str:
    with transaction.atomic():
        push = _locked(push_id)
        if push.status in (Push.SENT, Push.FAILED):
            return ""
        return _fail_locked(push, order, reason)


def _fail_locked(push: PosterOrderPush, order: Order, reason: Reason) -> str:
    settings = PartnerBotSettings.objects.filter(partner_id=order.partner_id).first()
    push.status = Push.FAILED
    push.last_error = gettext_in(main_language(settings), reason.text, **reason.params)
    push.save(update_fields=["status", "last_error", "updated_at"])
    if reason.maybe_sent:
        advice = N_("Проверьте онлайн-заказы на кассе Poster: если его там нет, пробейте его "
                    "вручную. Причина: {reason}.")
    else:
        advice = N_("Пробейте его на кассе Poster вручную. Причина: {reason}.")
    notify_staff_about_order_problem(
        order,
        title=lambda: _("Заказ #{code} не дошёл до Poster", code=order.public_id),
        message=lambda: _(advice, reason=str(reason)),
    )
    logger.warning("Order %s not sent to Poster: %s", order.pk, push.last_error)
    return Push.FAILED


# --- recovery ---------------------------------------------------------------------


def recover_pushes(now=None) -> int:
    """Pushes a lost task or a stopped worker left: queued again. One left
    in ``sending`` may have reached Poster — it becomes ``unknown`` and is
    looked for first."""

    from apps.integrations.poster.tasks import push_order_task

    now = now or timezone.now()
    with transaction.atomic():
        stuck = list(
            PosterOrderPush.objects.select_for_update(skip_locked=True)
            .filter(status=Push.SENDING, claimed_at__lt=now - STALE_SENDING)
        )
        for push in stuck:
            push.status = Push.UNKNOWN
            push.last_error = "The worker stopped while sending"
            push.save(update_fields=["status", "last_error", "updated_at"])
        ids = [push.pk for push in stuck]
        ids += list(
            PosterOrderPush.objects.filter(status=Push.PENDING,
                                           created_at__lt=now - STALE_PENDING)
            .values_list("pk", flat=True)
        )
        ids += list(
            PosterOrderPush.objects.filter(status=Push.UNKNOWN,
                                           claimed_at__lt=now - STALE_UNKNOWN)
            .exclude(pk__in=[push.pk for push in stuck])
            .values_list("pk", flat=True)
        )
    for push_id in ids:
        push_order_task.delay(str(push_id))
    return len(ids)
