"""Table billing: bills built from a table visit and guests' bill requests.

The table channel's own settlement (ADR-0007, ADR-0009). The channel-agnostic
bill core — creating a bill from orders, payments, bonuses, bill changes and
voiding — lives in ``apps/billing/services.py``; this module only decides
which orders of a table visit go into a bill and handles the «Запросить
счёт» requests of guests at a table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from itertools import groupby

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.billing.models import Bill, BillingRequest, BillItem
from apps.billing.services import (
    BillingServiceError,
    attach_orders_to_bill,
    create_bill_from_orders,
)
from apps.notifications.services import notify_staff_about_billing_request
from apps.orders.models import Order
from apps.partners.models import PartnerBotSettings
from apps.tables.models import TableSession, TableVisit
from apps.tables.services import (
    TableSessionError,
    can_close_table_session,
    close_table_session_if_settled,
    get_active_table_session,
)
from core.i18n import _


@dataclass(frozen=True, slots=True)
class BillingRequestResult:
    request: BillingRequest
    bill: Bill | None
    notified_count: int


_VISIT_NOT_GIVEN = object()


def resolve_table_visit_for_billing(*, partner_id, table_id, visit_id=_VISIT_NOT_GIVEN):
    """Return the visit id a table-level staff/Admin bill should use (ADR-0007).

    An explicit ``visit_id`` wins. Otherwise the table's single active visit is
    used; with no active visit the legacy orders without a visit are billed;
    with several active visits staff must choose one.
    """

    if visit_id is not _VISIT_NOT_GIVEN:
        return visit_id
    active_visit_ids = list(
        TableVisit.objects.filter(
            partner_id=partner_id,
            table_id=table_id,
            status=TableVisit.Status.ACTIVE,
        ).values_list("id", flat=True)
    )
    if len(active_visit_ids) > 1:
        raise BillingServiceError(
            _("За столом несколько визитов — выставьте счёт из карточки нужного визита.")
        )
    return active_visit_ids[0] if active_visit_ids else None


def get_table_billable_orders(
    *,
    partner_id,
    table_id,
    visit_id,
) -> list[Order]:
    """Unbilled, not cancelled orders of one table visit.

    ``visit_id=None`` selects the legacy orders of the table that belong to no
    visit (sessions closed before visits existed, or orders without session).
    """

    # Decided per order inside the database: loading every billed order id of
    # the partner made this slower with every month of history (load test
    # 2026-10-25).
    already_billed = Exists(
        BillItem.objects.filter(
            partner_id=partner_id,
            order_id=OuterRef("pk"),
            is_active=True,
            bill__status__in=[
                Bill.Status.DRAFT,
                Bill.Status.ISSUED,
                Bill.Status.PARTIALLY_PAID,
                Bill.Status.PAID,
            ],
        )
    )
    orders = list(
        Order.objects.select_related("table", "guest", "guest__telegram_account")
        .prefetch_related("items")
        .filter(
            partner_id=partner_id,
            table_id=table_id,
            table_session__visit_id=visit_id,
        )
        .exclude(status=Order.Status.CANCELED)
        .exclude(already_billed)
        .order_by("guest_id", "created_at")
    )
    if visit_id is None:
        # Orders placed without any table session also belong to no visit.
        orders += list(
            Order.objects.select_related("table", "guest", "guest__telegram_account")
            .prefetch_related("items")
            .filter(partner_id=partner_id, table_id=table_id, table_session__isnull=True)
            .exclude(status=Order.Status.CANCELED)
            .exclude(already_billed)
            .order_by("guest_id", "created_at")
        )
        orders.sort(key=lambda order: (str(order.guest_id), order.created_at))
    return orders



@transaction.atomic
def create_shared_bill_for_table(
    *,
    partner_id,
    table_id,
    label: str = "",
    visit_id=_VISIT_NOT_GIVEN,
) -> Bill:
    orders = get_table_billable_orders(
        partner_id=partner_id,
        table_id=table_id,
        visit_id=resolve_table_visit_for_billing(
            partner_id=partner_id,
            table_id=table_id,
            visit_id=visit_id,
        ),
    )
    if not orders:
        raise BillingServiceError(_("На этом столе нет заказов, доступных для нового счёта."))

    table = orders[0].table
    return create_bill_from_orders(
        partner_id=partner_id,
        order_ids=[str(order.id) for order in orders],
        label=label or f"Table #{table.number} shared bill",
        kind=Bill.Kind.SHARED,
    )



@transaction.atomic
def create_personal_bills_for_table(
    *,
    partner_id,
    table_id,
    visit_id=_VISIT_NOT_GIVEN,
) -> list[Bill]:
    orders = get_table_billable_orders(
        partner_id=partner_id,
        table_id=table_id,
        visit_id=resolve_table_visit_for_billing(
            partner_id=partner_id,
            table_id=table_id,
            visit_id=visit_id,
        ),
    )
    if not orders:
        raise BillingServiceError(_("На этом столе нет заказов, доступных для split bill."))

    bills: list[Bill] = []
    for _guest_id, guest_orders_iter in groupby(orders, key=lambda order: order.guest_id):
        guest_orders = list(guest_orders_iter)
        guest = guest_orders[0].guest
        guest_label = guest.telegram_account.username or guest.customer_code
        bills.append(
            create_bill_from_orders(
                partner_id=partner_id,
                order_ids=[str(order.id) for order in guest_orders],
                label=f"Table #{guest_orders[0].table.number} / {guest_label}",
                kind=Bill.Kind.PERSONAL,
            )
        )
    return bills


def get_or_create_personal_bill_for_guest_table(
    *,
    partner_id,
    table_id,
    guest_id,
    visit_id,
) -> Bill:
    open_bill = (
        Bill.objects.filter(
            partner_id=partner_id,
            table_id=table_id,
            visit_id=visit_id,
            primary_guest_id=guest_id,
            kind=Bill.Kind.PERSONAL,
            status__in=[Bill.Status.DRAFT, Bill.Status.ISSUED, Bill.Status.PARTIALLY_PAID],
        )
        .order_by("-created_at")
        .first()
    )
    guest_orders = [
        order
        for order in get_table_billable_orders(
            partner_id=partner_id,
            table_id=table_id,
            visit_id=visit_id,
        )
        if order.guest_id == guest_id
    ]
    if open_bill is not None:
        return attach_orders_to_bill(
            bill=open_bill,
            order_ids=[str(order.id) for order in guest_orders],
        )
    if not guest_orders:
        raise BillingServiceError(_("У этого гостя нет новых заказов для персонального счёта."))

    guest = guest_orders[0].guest
    guest_label = guest.telegram_account.username or guest.customer_code
    return create_bill_from_orders(
        partner_id=partner_id,
        order_ids=[str(order.id) for order in guest_orders],
        label=f"Table #{guest_orders[0].table.number} / {guest_label}",
        kind=Bill.Kind.PERSONAL,
    )


def get_or_create_shared_bill_for_table(
    *,
    partner_id,
    table_id,
    visit_id,
) -> Bill:
    open_bill = (
        Bill.objects.filter(
            partner_id=partner_id,
            table_id=table_id,
            visit_id=visit_id,
            kind=Bill.Kind.SHARED,
            status__in=[Bill.Status.DRAFT, Bill.Status.ISSUED, Bill.Status.PARTIALLY_PAID],
        )
        .order_by("-created_at")
        .first()
    )
    orders = get_table_billable_orders(
        partner_id=partner_id,
        table_id=table_id,
        visit_id=visit_id,
    )
    if open_bill is not None:
        return attach_orders_to_bill(
            bill=open_bill,
            order_ids=[str(order.id) for order in orders],
        )
    if not orders:
        raise BillingServiceError(_("На этом столе нет новых заказов для общего счёта."))
    return create_bill_from_orders(
        partner_id=partner_id,
        order_ids=[str(order.id) for order in orders],
        label=f"Table #{orders[0].table.number} shared bill",
        kind=Bill.Kind.SHARED,
    )



@transaction.atomic
def create_billing_request_for_telegram_user(
    *,
    partner_id,
    telegram_id: int,
    request_type: str,
    cooldown_seconds: int = 45,
) -> BillingRequestResult:
    table_session = get_active_table_session(partner_id=partner_id, telegram_id=telegram_id)
    if table_session is None:
        raise BillingServiceError(
            _("Запросить счёт можно только после активации стола по QR-коду.")
        )

    recent_duplicate = BillingRequest.objects.filter(
        partner_id=partner_id,
        table_session=table_session,
        guest=table_session.guest,
        request_type=request_type,
        created_at__gte=timezone.now() - timedelta(seconds=cooldown_seconds),
        status__in=[BillingRequest.Status.OPEN, BillingRequest.Status.AUTO_PREPARED],
    ).exists()
    if recent_duplicate:
        raise BillingServiceError(
            _("Похожий запрос счёта уже был отправлен совсем недавно. Подождите немного.")
        )

    bill = None
    status = BillingRequest.Status.OPEN
    note = ""
    if request_type == BillingRequest.RequestType.PERSONAL:
        bill = get_or_create_personal_bill_for_guest_table(
            partner_id=partner_id,
            table_id=table_session.table_id,
            guest_id=table_session.guest_id,
            visit_id=table_session.visit_id,
        )
        status = BillingRequest.Status.AUTO_PREPARED
        note = "Personal draft bill prepared automatically."
    elif request_type == BillingRequest.RequestType.SHARED:
        bill = get_or_create_shared_bill_for_table(
            partner_id=partner_id,
            table_id=table_session.table_id,
            visit_id=table_session.visit_id,
        )
        status = BillingRequest.Status.AUTO_PREPARED
        note = "Shared draft bill prepared automatically."
    elif request_type != BillingRequest.RequestType.CUSTOM_SPLIT:
        raise BillingServiceError(_("Неизвестный тип запроса счёта."))

    billing_request = BillingRequest.objects.create(
        partner_id=partner_id,
        table=table_session.table,
        guest=table_session.guest,
        table_session=table_session,
        request_type=request_type,
        status=status,
        bill=bill,
        note=note,
    )
    notified_count = notify_staff_about_billing_request(billing_request)
    return BillingRequestResult(
        request=billing_request,
        bill=bill,
        notified_count=notified_count,
    )



@transaction.atomic
def mark_billing_request_processed(billing_request: BillingRequest) -> BillingRequest:
    try:
        billing_request = BillingRequest.objects.select_for_update().get(
            id=billing_request.id,
            partner_id=billing_request.partner_id,
        )
    except BillingRequest.DoesNotExist as exc:
        raise BillingServiceError(_("Запрос счёта не найден в этом заведении.")) from exc
    if billing_request.status == BillingRequest.Status.CANCELED:
        raise BillingServiceError(_("Нельзя обработать уже отменённый запрос счёта."))
    if billing_request.status != BillingRequest.Status.PROCESSED:
        billing_request.status = BillingRequest.Status.PROCESSED
        billing_request.processed_at = timezone.now()
        billing_request.save(update_fields=["status", "processed_at", "updated_at"])
    return billing_request



@transaction.atomic
def cancel_billing_request(billing_request: BillingRequest) -> BillingRequest:
    try:
        billing_request = BillingRequest.objects.select_for_update().get(
            id=billing_request.id,
            partner_id=billing_request.partner_id,
        )
    except BillingRequest.DoesNotExist as exc:
        raise BillingServiceError(_("Запрос счёта не найден в этом заведении.")) from exc
    if billing_request.status == BillingRequest.Status.PROCESSED:
        raise BillingServiceError(_("Нельзя отменить уже обработанный запрос счёта."))
    if billing_request.status != BillingRequest.Status.CANCELED:
        billing_request.status = BillingRequest.Status.CANCELED
        billing_request.processed_at = timezone.now()
        billing_request.save(update_fields=["status", "processed_at", "updated_at"])
    return billing_request


def after_table_bill_paid(bill: Bill) -> None:
    """Table follow-up of a paid bill: mark its requests processed and, if the
    venue wants it, close the sessions it settled (PQ-027). Never raises into
    the payment (AA-021)."""

    _mark_related_billing_requests_processed(bill)
    if _partner_auto_closes_table_session_after_payment(partner_id=bill.partner_id):
        _close_settled_sessions_of_bill(bill)


def _partner_auto_closes_table_session_after_payment(*, partner_id) -> bool:
    setting_value = (
        PartnerBotSettings.objects.filter(partner_id=partner_id)
        .values_list("auto_close_table_session_after_payment", flat=True)
        .first()
    )
    if setting_value is None:
        return True
    return setting_value


def _mark_related_billing_requests_processed(bill: Bill) -> int:
    requests = BillingRequest.objects.filter(
        partner_id=bill.partner_id,
        bill=bill,
        status__in=[
            BillingRequest.Status.OPEN,
            BillingRequest.Status.AUTO_PREPARED,
        ],
    )
    processed_count = 0
    for billing_request in requests:
        mark_billing_request_processed(billing_request)
        processed_count += 1
    return processed_count


def _close_settled_sessions_of_bill(bill: Bill) -> int:
    """Auto-close the sessions whose orders this bill settled (PQ-027).

    Only sessions that own at least one order of the bill are candidates; a
    session is closed when it is fully settled, otherwise it stays open. This
    step never raises into the payment command (AA-021).
    """

    session_ids = set(
        Order.objects.filter(
            partner_id=bill.partner_id,
            bill_links__bill_id=bill.id,
            table_session_id__isnull=False,
        ).values_list("table_session_id", flat=True)
    )
    candidate_sessions = TableSession.objects.filter(
        partner_id=bill.partner_id,
        id__in=session_ids,
        status=TableSession.Status.ACTIVE,
    ).order_by("id")

    closed_count = 0
    for session in candidate_sessions:
        is_settled, _reason = can_close_table_session(
            session,
            include_billing_requests=False,
        )
        if not is_settled:
            continue
        # Every order of the session is paid and received, so pending
        # requests of this session are satisfied.
        pending_requests = BillingRequest.objects.filter(
            partner_id=bill.partner_id,
            table_session_id=session.id,
            status__in=[
                BillingRequest.Status.OPEN,
                BillingRequest.Status.AUTO_PREPARED,
            ],
        ).order_by("id")
        for billing_request in pending_requests:
            mark_billing_request_processed(billing_request)
        try:
            closed_session = close_table_session_if_settled(session)
        except TableSessionError:
            continue
        if closed_session.status == TableSession.Status.CLOSED:
            closed_count += 1
    return closed_count
