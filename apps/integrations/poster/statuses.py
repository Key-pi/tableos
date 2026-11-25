"""What the Poster register says back (PQ-058, step 6; PQ-059; plan —
docs/architecture/POSTER.md, «Статус из Poster»).

A webhook of an online order or a check (``incoming_order``,
``transaction``) that belongs to a Botera order — found among the pushes of
the signed account's venues by the online order's or the check's number
(``order_pushes``) — queues ``poster.sync_order_state``; the cashier's own
checks are not ours.

``sync_order_state`` reads the online order
(``incomingOrders.getIncomingOrder``) and its check (``dash.getTransaction``)
outside any transaction, keeps what the register said on the push and does
what it means:

- the check closed with money (``pay_type`` 1 cash, 2 card, 3 mixed) → the
  Botera bill paid that way and the order «Завершён»
  (``apps/billing/remote_orders.py:record_register_payment``). The register
  counts the money (owner 2026-11-24, PQ-061): less money (``payed_sum``)
  than the bill — a Poster discount, promotion or bonuses — pays the bill,
  the gap is a «Разница с чеком кассы» record; more is marked for Admin and
  the bill paid as it is; no money at all is left to staff;
- rejected (online order 7, check 4) → the order canceled and its bill
  voided (``cancel_remote_order_by_register``); the guest gets the usual
  message;
- accepted (1) while «Новый» → «Принят»; the guest gets the usual message;
- removed (``status`` 3: a return or a cancel at the register, PQ-061) →
  the money the register took comes back as a «На кассе» refund, an unpaid
  order is canceled (``undo_remote_order_by_register``);
- closed without money, removed when the money was taken in Botera, money
  for an order Botera canceled, a rejected order already paid → nothing
  automatic: «Требует внимания» on the push and a message to staff, once.

Paid in advance by transfer (v2, owner 2026-11-24, PQ-061) — the register
holds the transfer, not its money: accepted → the transfer confirmed and
«Принят», the closed check → «Завершён»
(``apps/billing/remote_orders.py:confirm_transfer_by_register``); rejected,
or the check deleted before the transfer was recorded → canceled as above,
and staff are told to return the transfer if it came; the check deleted
after it → staff return it and record the refund (the register gives
nothing back, spike D3).

Every step checks again under its own locks (Bill → Order for money, Order
for a status), so the two webhooks Poster sends a second apart do it once.
The push row is locked only to keep what Poster said and the mark.

A webhook Poster never delivered (PQ-061): ``recheck_open_orders`` — the
beat task ``poster.recheck_open_orders`` every 5 minutes — queues the same
``sync_order_state`` for each order still open in Botera that Poster said
nothing about for 10 minutes, until 2 days after sending; not for an order
the register is done with, a venue off Poster or suspended. Reading the
state brings the push's ``updated_at`` forward, so it waits again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.billing.models import Bill, Payment
from apps.billing.remote_orders import (
    RemoteOrderError,
    cancel_remote_order_by_register,
    confirm_transfer_by_register,
    record_register_payment,
    undo_remote_order_by_register,
)
from apps.billing.services import BillingServiceError
from apps.integrations.poster.client import PosterClient, PosterError
from apps.integrations.poster.models import PosterConnection, PosterOrderPush
from apps.integrations.poster.orders import Reason
from apps.integrations.poster.selectors import CHECK_REJECTED, CHECK_REMOVED, ONLINE_REJECTED
from apps.notifications.services import notify_staff_about_order_problem
from apps.orders.models import Order
from apps.orders.services import OrderFlowError, transition_order_status
from apps.partners.languages import main_language
from apps.partners.models import Partner, PartnerBotSettings
from core.i18n import N_, _, gettext_in

logger = logging.getLogger(__name__)

Push = PosterOrderPush.Status
ACCEPTED, REJECTED = 1, ONLINE_REJECTED
CHECK_CLOSED = 2
PAY_METHODS = {1: Payment.Method.CASH, 2: Payment.Method.TERMINAL, 3: Payment.Method.MIXED}
PROVIDER = "poster"
RETRY_DELAYS = (60, 120, 300)  # seconds, when Poster did not answer
# A webhook Poster never delivered (PQ-061): an order it said nothing about
# for RECHECK_QUIET is read again, for RECHECK_FOR after sending.
RECHECK_QUIET = timedelta(minutes=10)
RECHECK_FOR = timedelta(days=2)
RECHECK_BATCH = 200


@dataclass
class RegisterState:
    online: int | None
    check: int | None
    pay_type: int | None
    total: Decimal | None
    paid: Decimal | None
    reason: str
    transaction_id: int | None


def _number(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _hryvnias(kopecks) -> Decimal | None:
    number = _number(kopecks)
    return None if number is None else (Decimal(number) / 100).quantize(Decimal("0.01"))


# --- the webhook ------------------------------------------------------------------


def order_pushes(event, connections) -> list[PosterOrderPush]:
    """The Botera orders a webhook of an online order or a check is about."""

    field = "incoming_order_id" if event.entity == "incoming_order" else "transaction_id"
    number = _number(event.entity_id)
    if number is None:
        return []
    return list(PosterOrderPush.objects.filter(
        partner_id__in=[connection.partner_id for connection in connections],
        status=Push.SENT, **{field: number},
    ))


# --- reading Poster ---------------------------------------------------------------


def _read_state(token: str, push: PosterOrderPush) -> RegisterState:
    """The online order and its check; network only."""

    with PosterClient(token) as client:
        online = client.call("incomingOrders.getIncomingOrder",
                             params={"incoming_order_id": push.incoming_order_id})
        online = online if isinstance(online, dict) else {}
        transaction_id = push.transaction_id or _number(online.get("transaction_id"))
        check: dict = {}
        if transaction_id:
            answer = client.call("dash.getTransaction", params={
                "transaction_id": transaction_id, "include_products": "true", "status": 0,
            })
            if isinstance(answer, list) and answer and isinstance(answer[0], dict):
                check = answer[0]
            elif isinstance(answer, dict):
                check = answer
    return RegisterState(
        online=_number(online.get("status")),
        check=_number(check.get("status")) if check else None,
        pay_type=_number(check.get("pay_type")) if check else None,
        total=_hryvnias(check.get("sum")) if check else None,
        paid=_hryvnias(check.get("payed_sum")) if check else None,
        reason=str(check.get("reason") or ""),
        transaction_id=transaction_id,
    )


def _keep_state(push_id, state: RegisterState) -> None:
    with transaction.atomic():
        push = PosterOrderPush.objects.select_for_update().get(pk=push_id)
        push.poster_status = state.online
        push.check_status = state.check
        push.pay_type = state.pay_type
        push.poster_sum = state.total
        push.poster_payed_sum = state.paid
        if push.transaction_id is None and state.transaction_id:
            push.transaction_id = state.transaction_id
        push.save(update_fields=[
            "poster_status", "check_status", "pay_type", "poster_sum", "poster_payed_sum",
            "transaction_id", "updated_at",
        ])


def _retry_later(push_id: str, attempt: int) -> None:
    from apps.integrations.poster.tasks import sync_order_state_task

    sync_order_state_task.apply_async((push_id, attempt + 1),
                                      countdown=RETRY_DELAYS[attempt - 1])


# --- doing what the register said ---------------------------------------------------


def _language(partner_id) -> str:
    return main_language(PartnerBotSettings.objects.filter(partner_id=partner_id).first())


CHECK_IN_POSTER = (N_("Заказ #{code}: проверьте в Poster"),
                   N_("{reason}. Решите вручную в карточке заказа."))
# Paid in advance (v2): the register returns nothing of a transfer.
RETURN_IF_CAME = (N_("Заказ #{code}: верните перевод гостю"),
                  N_("{reason}. Если деньги пришли — верните их гостю."))
RETURN_AND_RECORD = (N_("Заказ #{code}: верните перевод гостю"),
                     N_("{reason}. Верните деньги гостю и отметьте возврат в карточке заказа."))


def _mark(push_id, order: Order, reason: Reason, *, tell_staff: bool = True,
          words: tuple[str, str] = CHECK_IN_POSTER) -> str:
    """«Требует внимания» on the push; staff are told once per new mark,
    ``words`` — the message's title and text."""

    title, message = words
    with transaction.atomic():
        push = PosterOrderPush.objects.select_for_update().get(pk=push_id)
        text = gettext_in(_language(order.partner_id), reason.text, **reason.params)[:255]
        if push.attention == text:
            return ""
        push.attention = text
        push.save(update_fields=["attention", "updated_at"])
        if tell_staff:
            notify_staff_about_order_problem(
                order,
                title=lambda: _(title, code=order.public_id),
                message=lambda: _(message, reason=str(reason)),
            )
    return "attention"


def _bill_total(order: Order) -> Decimal | None:
    return (
        Bill.objects.filter(bill_orders__order=order).exclude(status=Bill.Status.CANCELED)
        .values_list("total_amount", flat=True).first()
    )


def _pay(push: PosterOrderPush, order: Order, state: RegisterState) -> str:
    check_number = state.transaction_id or ""
    method = PAY_METHODS.get(state.pay_type)
    if method is None or (state.paid is not None and state.paid <= 0):
        # Nothing taken, or no money: the whole check by Poster bonuses or a full discount.
        return _mark(push.pk, order, Reason(
            N_("чек Poster №{check} закрыт без оплаты (причина {reason})"),
            {"check": check_number, "reason": state.reason or "—"},
        ))
    try:
        record_register_payment(
            order=order, method=method, provider_code=PROVIDER, reference=str(check_number),
            note=gettext_in(_language(order.partner_id), "Оплачен на кассе Poster, чек №{check}.",
                            check=check_number),
            register_paid=state.paid,
        )
    except RemoteOrderError as exc:
        return _mark(push.pk, order, Reason(
            N_("чек Poster №{check} оплачен, а заказ в Botera уже закрыт ({reason})"),
            {"check": check_number, "reason": str(exc).rstrip(".")},
        ))
    # Less than the bill follows the check (``register_paid``); more is not this order.
    bill_total = _bill_total(order)
    took = state.paid if state.paid is not None else state.total
    if took is not None and bill_total is not None and took > bill_total:
        return _mark(push.pk, order, Reason(
            N_("сумма чека Poster {check_sum} грн, счёт Botera {bill_sum} грн"),
            {"check_sum": took, "bill_sum": bill_total},
        ), tell_staff=False)
    return "paid"


def _prepaid(order: Order) -> bool:
    return order.payment_timing == Order.PaymentTiming.PREPAID


def _cancel(push: PosterOrderPush, order: Order) -> str:
    try:
        canceled = cancel_remote_order_by_register(
            order=order, note=gettext_in(_language(order.partner_id),
                                         "Отклонён на кассе Poster."),
        )
    except RemoteOrderError:
        if _prepaid(order):
            return _transfer_recorded(push, order, state=None)
        return _mark(push.pk, order, Reason(
            N_("заказ отклонён на кассе Poster, а по нему уже есть оплата в Botera"),
        ))
    if canceled and _prepaid(order):
        _transfer_not_kept(push, order)
    return "canceled" if canceled else ""


def _transfer_not_kept(push: PosterOrderPush, order: Order) -> None:
    """A prepaid order canceled at the register: the guest may have sent
    the money."""

    _mark(push.pk, order, Reason(N_("касса Poster отклонила заказ, оплаченный переводом")),
          words=RETURN_IF_CAME)


def _transfer_recorded(push: PosterOrderPush, order: Order, state: RegisterState | None) -> str:
    """Undone at the register after the transfer was recorded."""

    check_number = (state.transaction_id if state else None) or push.transaction_id or ""
    return _mark(push.pk, order, Reason(
        N_("чек Poster №{check} отменён на кассе, а заказ оплачен переводом"),
        {"check": check_number},
    ), words=RETURN_AND_RECORD)


def _settle_transfer(push: PosterOrderPush, order: Order, state: RegisterState, *,
                     complete: bool) -> str:
    """"changed", "" on a repeat, or the mark when Botera closed the order."""

    check_number = state.transaction_id or push.transaction_id or ""
    try:
        changed = confirm_transfer_by_register(
            order=order, provider_code=PROVIDER, reference=str(check_number),
            note=gettext_in(_language(order.partner_id),
                            "Перевод подтверждён на кассе Poster, чек №{check}.",
                            check=check_number),
            complete=complete,
        )
    except RemoteOrderError as exc:
        return _mark(push.pk, order, Reason(
            N_("заказ, оплаченный переводом, принят на кассе Poster, а в Botera уже закрыт "
               "({reason})"),
            {"reason": str(exc).rstrip(".")},
        ))
    return "changed" if changed else ""


def _accept_prepaid(push: PosterOrderPush, order: Order, state: RegisterState) -> str:
    """«Прийняти» of a prepaid order: the cashier checked the transfer."""

    result = _settle_transfer(push, order, state, complete=False)
    return "accepted" if result == "changed" else result


def _closed_prepaid(push: PosterOrderPush, order: Order, state: RegisterState) -> str:
    """The check of a prepaid order closed: the order handed over."""

    result = _settle_transfer(push, order, state, complete=True)
    if result != "changed":
        return result
    bill_total = _bill_total(order)
    if state.total is not None and bill_total is not None and state.total != bill_total:
        # Botera's prices went with the payment: the check should be the transfer.
        return _mark(push.pk, order, Reason(
            N_("сумма чека Poster {check_sum} грн, счёт Botera {bill_sum} грн"),
            {"check_sum": state.total, "bill_sum": bill_total},
        ), tell_staff=False)
    return "paid"


def _removed(push: PosterOrderPush, order: Order, state: RegisterState) -> str:
    """The check deleted in Poster — a return or a cancel at the register."""

    check_number = state.transaction_id or ""
    try:
        undone = undo_remote_order_by_register(
            order=order, provider_code=PROVIDER, reference=str(check_number),
            note=gettext_in(_language(order.partner_id), "Чек Poster №{check} удалён на кассе.",
                            check=check_number),
        )
    except (RemoteOrderError, BillingServiceError):
        if _prepaid(order):
            return _transfer_recorded(push, order, state)
        return _mark(push.pk, order, Reason(N_("чек Poster №{check} удалён"),
                                            {"check": check_number}))
    if undone == "canceled" and _prepaid(order):
        _transfer_not_kept(push, order)
    return undone


def _accept(order: Order) -> str:
    if order.status != Order.Status.NEW:
        return ""
    try:
        transition_order_status(
            order=order, to_status=Order.Status.ACCEPTED, actor_user=None,
            note=gettext_in(_language(order.partner_id), "Принят на кассе Poster."),
        )
    except OrderFlowError:
        return ""  # moved on meanwhile: someone else did it
    return "accepted"


def apply_state(push: PosterOrderPush, state: RegisterState) -> str:
    order = Order.objects.select_related("partner").get(pk=push.order_id)
    if state.online == REJECTED or state.check == CHECK_REJECTED:
        return _cancel(push, order)
    if state.check == CHECK_CLOSED:
        return _closed_prepaid(push, order, state) if _prepaid(order) else _pay(
            push, order, state)
    if state.check == CHECK_REMOVED:
        return _removed(push, order, state)
    if state.online == ACCEPTED:
        return _accept_prepaid(push, order, state) if _prepaid(order) else _accept(order)
    return ""


def sync_order_state(push_id, *, attempt: int = 1) -> str:
    """Bring what the register said about one Botera order; what was done."""

    push = PosterOrderPush.objects.filter(pk=push_id).first()
    if push is None or push.status != Push.SENT or push.incoming_order_id is None:
        return ""
    connection = PosterConnection.objects.filter(
        partner_id=push.partner_id, status=PosterConnection.Status.ACTIVE,
    ).first()
    token = connection.get_access_token() if connection is not None else ""
    if not token:
        return ""
    # Network first, outside any transaction.
    try:
        state = _read_state(token, push)
    except PosterError as exc:
        logger.warning("Poster state of push %s not read: %s", push_id, exc.message)
        if exc.temporary and attempt <= len(RETRY_DELAYS):
            _retry_later(str(push_id), attempt)
        return ""
    _keep_state(push.pk, state)
    return apply_state(push, state)


def recheck_open_orders(now=None) -> int:
    """Orders Poster may have lost a webhook of: queued for
    ``sync_order_state``, the quietest first; how many."""

    from apps.integrations.poster.tasks import sync_order_state_task

    now = now or timezone.now()
    working = PosterConnection.objects.filter(
        partner_id=OuterRef("partner_id"), status=PosterConnection.Status.ACTIVE,
    )
    ids = list(
        PosterOrderPush.objects.filter(
            status=Push.SENT, incoming_order_id__isnull=False,
            sent_at__gte=now - RECHECK_FOR, updated_at__lt=now - RECHECK_QUIET,
        )
        .filter(Exists(working))
        .exclude(partner__status=Partner.Status.SUSPENDED)
        .exclude(order__status__in=(Order.Status.COMPLETED, Order.Status.CANCELED))
        # The register is done with it: what is left is staff's.
        .exclude(check_status__in=(CHECK_REMOVED, CHECK_REJECTED))
        .exclude(poster_status=REJECTED)
        .order_by("updated_at")
        .values_list("pk", flat=True)[:RECHECK_BATCH]
    )
    for push_id in ids:
        sync_order_state_task.delay(str(push_id))
    return len(ids)
