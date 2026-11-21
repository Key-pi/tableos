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
  rest of the Botera bill paid that way and the order «Завершён»
  (``apps/billing/remote_orders.py:record_register_payment``); a check sum
  other than the bill's is marked for Admin, the money in Botera stays;
- rejected (online order 7, check 4) → the order canceled and its bill
  voided (``cancel_remote_order_by_register``); the guest gets the usual
  message;
- accepted (1) while «Новый» → «Принят»; the guest gets the usual message;
- closed without money, removed, money for an order Botera canceled, a
  rejected order already paid → nothing automatic: «Требует внимания» on the
  push and a message to staff, once.

Every step checks again under its own locks (Bill → Order for money, Order
for a status), so the two webhooks Poster sends a second apart do it once.
The push row is locked only to keep what Poster said and the mark.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from apps.billing.models import Bill, Payment
from apps.billing.remote_orders import (
    RemoteOrderError,
    cancel_remote_order_by_register,
    record_register_payment,
)
from apps.integrations.poster.client import PosterClient, PosterError
from apps.integrations.poster.models import PosterConnection, PosterOrderPush
from apps.integrations.poster.orders import Reason
from apps.notifications.services import notify_staff_about_order_problem
from apps.orders.models import Order
from apps.orders.services import OrderFlowError, transition_order_status
from apps.partners.languages import main_language
from apps.partners.models import PartnerBotSettings
from core.i18n import N_, _, gettext_in

logger = logging.getLogger(__name__)

Push = PosterOrderPush.Status
ACCEPTED, REJECTED = 1, 7
CHECK_CLOSED, CHECK_REMOVED, CHECK_REJECTED = 2, 3, 4
PAY_METHODS = {1: Payment.Method.CASH, 2: Payment.Method.TERMINAL, 3: Payment.Method.MIXED}
PROVIDER = "poster"
RETRY_DELAYS = (60, 120, 300)  # seconds, when Poster did not answer


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


def _mark(push_id, order: Order, reason: Reason, *, tell_staff: bool = True) -> str:
    """«Требует внимания» on the push; staff are told once per new mark."""

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
                title=lambda: _("Заказ #{code}: проверьте в Poster", code=order.public_id),
                message=lambda: _("{reason}. Решите вручную в карточке заказа.",
                                  reason=str(reason)),
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
    if method is None:
        return _mark(push.pk, order, Reason(
            N_("чек Poster №{check} закрыт без оплаты (причина {reason})"),
            {"check": check_number, "reason": state.reason or "—"},
        ))
    try:
        record_register_payment(
            order=order, method=method, provider_code=PROVIDER, reference=str(check_number),
            note=gettext_in(_language(order.partner_id), "Оплачен на кассе Poster, чек №{check}.",
                            check=check_number),
        )
    except RemoteOrderError as exc:
        return _mark(push.pk, order, Reason(
            N_("чек Poster №{check} оплачен, а заказ в Botera уже закрыт ({reason})"),
            {"check": check_number, "reason": str(exc).rstrip(".")},
        ))
    bill_total = _bill_total(order)
    if state.total is not None and bill_total is not None and state.total != bill_total:
        return _mark(push.pk, order, Reason(
            N_("сумма чека Poster {check_sum} грн, счёт Botera {bill_sum} грн"),
            {"check_sum": state.total, "bill_sum": bill_total},
        ), tell_staff=False)
    return "paid"


def _cancel(push: PosterOrderPush, order: Order) -> str:
    try:
        canceled = cancel_remote_order_by_register(
            order=order, note=gettext_in(_language(order.partner_id),
                                         "Отклонён на кассе Poster."),
        )
    except RemoteOrderError:
        return _mark(push.pk, order, Reason(
            N_("заказ отклонён на кассе Poster, а по нему уже есть оплата в Botera"),
        ))
    return "canceled" if canceled else ""


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
        return _pay(push, order, state)
    if state.check == CHECK_REMOVED:
        return _mark(push.pk, order, Reason(N_("чек Poster №{check} удалён"),
                                            {"check": state.transaction_id or ""}))
    if state.online == ACCEPTED:
        return _accept(order)
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
