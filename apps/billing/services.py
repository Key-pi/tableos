from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from itertools import groupby

from django.db import transaction
from django.utils import timezone

from apps.billing.models import (
    Bill,
    BillAdjustment,
    BillingRequest,
    BillItem,
    BillOrder,
    Payment,
)
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.bonuses.services import (
    BonusServiceError,
    apply_bonus_programs,
    assert_bonus_balance_reconciled,
    get_redeemable_bonus_amount,
    sync_bonus_balance_from_ledger,
)
from apps.employees.models import EmployeeProfile
from apps.notifications.services import notify_staff_about_billing_request
from apps.orders.models import Order, OrderItem
from apps.orders.services import transition_order_status
from apps.partners.models import PartnerBotSettings
from apps.tables.models import TableSession, TableVisit
from apps.tables.services import (
    TableSessionError,
    can_close_table_session,
    close_table_session_if_settled,
    get_active_table_session,
)
from apps.users.models import AdminAccessProfile, GuestProfile, User


class BillingServiceError(Exception):
    """Raised when a bill or payment operation violates billing rules."""


@dataclass(frozen=True, slots=True)
class BillingRequestResult:
    request: BillingRequest
    bill: Bill | None
    notified_count: int


@dataclass(frozen=True, slots=True)
class BonusPaymentResult:
    """Result of applying the guest's allowed bonus balance to a bill."""

    bill: Bill
    bonus_spent_amount: Decimal
    remaining_amount: Decimal
    payment: Payment | None = None


def get_bill_remaining_amount(bill: Bill) -> Decimal:
    """Return the unpaid remainder computed from items and payments.

    Read-only: it never saves the bill (AA-028), so it is safe in list views.
    """

    refreshed_bill = Bill.objects.prefetch_related("items", "payments").get(id=bill.id)
    _subtotal, total_amount, paid_amount = _compute_bill_totals(refreshed_bill)
    return (total_amount - paid_amount).quantize(Decimal("0.01"))


def get_bill_context_label(bill: Bill) -> str:
    if bill.table_id is not None:
        return f"стол #{bill.table.number}"
    if bill.primary_guest_id is not None:
        return f"гость {bill.primary_guest.customer_code}"
    return "заказ без привязки к столу"


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
            "За столом несколько визитов — выставьте счёт из карточки нужного визита."
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

    allocated_order_ids = set(
        BillItem.objects.filter(
            partner_id=partner_id,
            is_active=True,
            bill__status__in=[
                Bill.Status.DRAFT,
                Bill.Status.ISSUED,
                Bill.Status.PARTIALLY_PAID,
                Bill.Status.PAID,
            ],
        ).values_list("order_id", flat=True)
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
        .exclude(id__in=allocated_order_ids)
        .order_by("guest_id", "created_at")
    )
    if visit_id is None:
        # Orders placed without any table session also belong to no visit.
        orders += list(
            Order.objects.select_related("table", "guest", "guest__telegram_account")
            .prefetch_related("items")
            .filter(partner_id=partner_id, table_id=table_id, table_session__isnull=True)
            .exclude(status=Order.Status.CANCELED)
            .exclude(id__in=allocated_order_ids)
            .order_by("guest_id", "created_at")
        )
        orders.sort(key=lambda order: (str(order.guest_id), order.created_at))
    return orders


@transaction.atomic
def attach_orders_to_bill(
    *,
    bill: Bill,
    order_ids: list[str],
) -> Bill:
    try:
        bill = Bill.objects.select_for_update().get(
            id=bill.id,
            partner_id=bill.partner_id,
        )
    except Bill.DoesNotExist as exc:
        raise BillingServiceError("Счёт не найден в этом заведении.") from exc
    if bill.status in {Bill.Status.CANCELED, Bill.Status.PAID}:
        raise BillingServiceError("Нельзя добавлять заказы в закрытый счёт.")
    if not order_ids:
        return bill

    requested_order_ids = {str(order_id) for order_id in order_ids}
    existing_order_ids = set(bill.bill_orders.values_list("order_id", flat=True))
    requested_orders = list(
        Order.objects.select_related("table", "guest", "table_session")
        .prefetch_related("items")
        .filter(
            partner_id=bill.partner_id,
            id__in=requested_order_ids,
        )
    )
    if len(requested_orders) != len(requested_order_ids):
        raise BillingServiceError("Не все выбранные заказы найдены в этом заведении.")

    if bill.table_id is not None:
        invalid_context = [order for order in requested_orders if order.table_id != bill.table_id]
        if invalid_context:
            raise BillingServiceError("В этот счёт можно добавлять только заказы того же стола.")
        if any(_order_visit_id(order) != bill.visit_id for order in requested_orders):
            raise BillingServiceError("В этот счёт можно добавлять только заказы того же визита.")
    else:
        if any(order.table_id is not None for order in requested_orders):
            raise BillingServiceError(
                "В счёт без привязки к столу можно добавлять только такие же заказы."
            )
        if bill.primary_guest_id is not None:
            invalid_guest_orders = [
                order for order in requested_orders if order.guest_id != bill.primary_guest_id
            ]
            if invalid_guest_orders:
                raise BillingServiceError(
                    "В персональный счёт без стола можно добавлять только заказы этого гостя."
                )
        elif len({order.guest_id for order in requested_orders}) != 1:
            raise BillingServiceError(
                "Заказы без стола можно объединять только в рамках одного гостя."
            )

    orders = [order for order in requested_orders if order.id not in existing_order_ids]
    if not orders:
        return bill

    order_items = list(
        OrderItem.objects.select_for_update()
        .filter(order_id__in=[order.id for order in orders])
        .order_by("id")
    )

    if any(order.status == Order.Status.CANCELED for order in orders):
        raise BillingServiceError("Отменённые заказы нельзя включать в счёт.")

    target_order_item_ids = [order_item.id for order_item in order_items]
    allocated_order_item_ids = set(
        BillItem.objects.filter(
            partner_id=bill.partner_id,
            is_active=True,
            order_item_id__in=target_order_item_ids,
            bill__status__in=[
                Bill.Status.DRAFT,
                Bill.Status.ISSUED,
                Bill.Status.PARTIALLY_PAID,
                Bill.Status.PAID,
            ],
        )
        .exclude(bill_id=bill.id)
        .values_list("order_item_id", flat=True)
    )
    if allocated_order_item_ids:
        raise BillingServiceError("Часть позиций уже включена в другой счёт.")

    BillOrder.objects.bulk_create(
        [
            BillOrder(
                partner_id=bill.partner_id,
                bill=bill,
                order=order,
            )
            for order in orders
        ]
    )
    BillItem.objects.bulk_create(
        [
            BillItem(
                partner_id=bill.partner_id,
                bill=bill,
                order=order,
                order_item=order_item,
                item_name=order_item.item_name,
                unit_price=order_item.unit_price,
                quantity=order_item.quantity,
                line_total=(order_item.unit_price * order_item.quantity).quantize(
                    Decimal("0.01")
                ),
                comment=order_item.comment,
            )
            for order in orders
            for order_item in order_items
            if order_item.order_id == order.id
        ]
    )
    bill = Bill.objects.prefetch_related("items", "payments", "bill_orders").get(id=bill.id)
    return recalculate_bill_totals(bill)


def _compute_bill_totals(bill: Bill) -> tuple[Decimal, Decimal, Decimal]:
    """Return (subtotal, total, money paid) from the bill's items and payments."""

    subtotal = sum(
        (item.line_total for item in bill.items.all() if item.is_active),
        Decimal("0.00"),
    )
    paid_amount = sum(
        (
            payment.amount
            for payment in bill.payments.filter(status=Payment.Status.PAID)
            if payment.method != Payment.Method.BONUSES
        ),
        Decimal("0.00"),
    )
    total_amount = max(
        Decimal("0.00"),
        subtotal - bill.discount_amount - bill.bonus_spent_amount,
    ).quantize(Decimal("0.01"))
    return subtotal, total_amount, paid_amount.quantize(Decimal("0.01"))


def _order_visit_id(order: Order):
    return order.table_session.visit_id if order.table_session_id else None


def recalculate_bill_totals(bill: Bill) -> Bill:
    subtotal, total_amount, paid_amount = _compute_bill_totals(bill)
    bill.subtotal_amount = subtotal
    bill.total_amount = total_amount
    bill.paid_amount = paid_amount
    bill.save(
        update_fields=[
            "discount_amount",
            "bonus_spent_amount",
            "subtotal_amount",
            "total_amount",
            "paid_amount",
            "updated_at",
        ]
    )
    return bill


@transaction.atomic
def create_bill_from_orders(
    *,
    partner_id,
    order_ids: list[str],
    label: str = "",
    kind: str = Bill.Kind.SHARED,
    created_source: str = Bill.Source.INTERNAL,
) -> Bill:
    if not order_ids:
        raise BillingServiceError("Нужно выбрать хотя бы один заказ для создания счёта.")

    requested_order_ids = {str(order_id) for order_id in order_ids}
    # PostgreSQL rejects FOR UPDATE on the nullable side of an outer join
    # (`table` is nullable), so lock only the order rows themselves.
    orders = list(
        Order.objects.select_for_update(of=("self",))
        .select_related("table", "guest", "table_session")
        .prefetch_related("items")
        .filter(
            partner_id=partner_id,
            id__in=requested_order_ids,
        )
    )
    if len(orders) != len(requested_order_ids):
        raise BillingServiceError("Не все выбранные заказы найдены в этом заведении.")

    if any(order.status == Order.Status.CANCELED for order in orders):
        raise BillingServiceError("Отменённые заказы нельзя включать в новый счёт.")

    table_ids = {order.table_id for order in orders}
    if len(table_ids) != 1:
        raise BillingServiceError(
            "В один счёт можно объединять только заказы одного стола или одного гостя без стола."
        )
    if next(iter(table_ids)) is None and len({order.guest_id for order in orders}) != 1:
        raise BillingServiceError(
            "Заказы без стола можно объединять только в рамках одного гостя."
        )
    visit_ids = {_order_visit_id(order) for order in orders}
    if len(visit_ids) != 1:
        # ADR-0007: orders of one table visit never enter another visit's bill.
        raise BillingServiceError("В один счёт можно объединять только заказы одного визита.")
    (bill_visit_id,) = visit_ids

    order_items = list(
        OrderItem.objects.select_for_update()
        .filter(order_id__in=[order.id for order in orders])
        .order_by("id")
    )

    allocated_order_item_ids = set(
        BillItem.objects.filter(
            partner_id=partner_id,
            is_active=True,
            order_item_id__in=[order_item.id for order_item in order_items],
            bill__status__in=[
                Bill.Status.DRAFT,
                Bill.Status.ISSUED,
                Bill.Status.PARTIALLY_PAID,
                Bill.Status.PAID,
            ],
        ).values_list("order_item_id", flat=True)
    )
    if allocated_order_item_ids:
        raise BillingServiceError(
            "Один или несколько order items уже включены в другой счёт."
        )

    primary_guest = orders[0].guest if len({order.guest_id for order in orders}) == 1 else None
    bill = Bill.objects.create(
        partner_id=partner_id,
        table=orders[0].table if orders[0].table_id is not None else None,
        visit_id=bill_visit_id,
        primary_guest=primary_guest,
        kind=kind,
        source=created_source,
        label=label,
    )

    bill_orders = [
        BillOrder(
            partner_id=partner_id,
            bill=bill,
            order=order,
        )
        for order in orders
    ]
    BillOrder.objects.bulk_create(bill_orders)

    bill_items: list[BillItem] = []
    for order in orders:
        for order_item in order_items:
            if order_item.order_id != order.id:
                continue
            bill_items.append(
                BillItem(
                    partner_id=partner_id,
                    bill=bill,
                    order=order,
                    order_item=order_item,
                    item_name=order_item.item_name,
                    unit_price=order_item.unit_price,
                    quantity=order_item.quantity,
                    line_total=(order_item.unit_price * order_item.quantity).quantize(
                        Decimal("0.01")
                    ),
                    comment=order_item.comment,
                )
            )
    BillItem.objects.bulk_create(bill_items)
    bill = Bill.objects.prefetch_related("items", "payments").get(id=bill.id)
    return recalculate_bill_totals(bill)


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
        raise BillingServiceError("На этом столе нет заказов, доступных для нового счёта.")

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
        raise BillingServiceError("На этом столе нет заказов, доступных для split bill.")

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
        raise BillingServiceError("У этого гостя нет новых заказов для персонального счёта.")

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
        raise BillingServiceError("На этом столе нет новых заказов для общего счёта.")
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
            "Запросить счёт можно только после активации стола по QR-коду."
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
            "Похожий запрос счёта уже был отправлен совсем недавно. Подождите немного."
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
        raise BillingServiceError("Неизвестный тип запроса счёта.")

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
def issue_bill(bill: Bill) -> Bill:
    try:
        bill = Bill.objects.select_for_update().get(
            id=bill.id,
            partner_id=bill.partner_id,
        )
    except Bill.DoesNotExist as exc:
        raise BillingServiceError("Счёт не найден в этом заведении.") from exc
    if bill.status != Bill.Status.DRAFT:
        raise BillingServiceError("Выдать можно только draft-счёт.")
    bill.status = Bill.Status.ISSUED
    bill.issued_at = timezone.now()
    bill.save(update_fields=["status", "issued_at", "updated_at"])
    return bill


@transaction.atomic
def record_payment(
    *,
    bill: Bill,
    amount: Decimal | str,
    method: str,
    created_by=None,
    comment: str = "",
    provider_code: str = "",
    external_payment_id: str = "",
    mark_bill_issued: bool = True,
) -> Payment:
    if method not in Payment.Method.values:
        raise BillingServiceError("Неизвестный способ оплаты.")
    if method == Payment.Method.BONUSES:
        raise BillingServiceError(
            "Оплату бонусами нужно запускать через команду оплаты бонусами."
        )
    try:
        amount_value = Decimal(amount).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BillingServiceError("Сумма оплаты должна быть корректным денежным значением.") from exc
    if not amount_value.is_finite():
        raise BillingServiceError("Сумма оплаты должна быть конечным денежным значением.")
    if amount_value <= 0:
        raise BillingServiceError("Сумма оплаты должна быть больше нуля.")

    bill = Bill.objects.select_for_update().prefetch_related("items", "payments").get(
        id=bill.id,
        partner_id=bill.partner_id,
    )
    if bill.status == Bill.Status.CANCELED:
        raise BillingServiceError("Нельзя принять оплату по отменённому счёту.")
    if bill.status == Bill.Status.PAID:
        raise BillingServiceError("Счёт уже полностью оплачен.")
    recalculate_bill_totals(bill)
    remaining_amount = (bill.total_amount - bill.paid_amount).quantize(Decimal("0.01"))
    if amount_value > remaining_amount:
        raise BillingServiceError("Сумма оплаты превышает остаток по счёту.")

    payment = Payment.objects.create(
        partner_id=bill.partner_id,
        bill=bill,
        status=Payment.Status.PAID,
        method=method,
        amount=amount_value,
        provider_code=provider_code,
        external_payment_id=external_payment_id,
        comment=comment,
        paid_at=timezone.now(),
        created_by=created_by,
    )

    bill = Bill.objects.prefetch_related("items", "payments").get(id=bill.id)
    recalculate_bill_totals(bill)
    if mark_bill_issued and bill.issued_at is None:
        bill.issued_at = timezone.now()
    if bill.paid_amount == bill.total_amount:
        bill.status = Bill.Status.PAID
        bill.closed_at = timezone.now()
    elif bill.paid_amount > 0:
        bill.status = Bill.Status.PARTIALLY_PAID
    elif mark_bill_issued and bill.status == Bill.Status.DRAFT:
        bill.status = Bill.Status.ISSUED
        bill.issued_at = timezone.now()

    update_fields = ["status", "updated_at"]
    if bill.closed_at is not None:
        update_fields.append("closed_at")
    if bill.issued_at is not None:
        update_fields.append("issued_at")
    bill.save(update_fields=update_fields)
    _process_post_payment_updates(bill)
    return payment


@transaction.atomic
def pay_bill_with_bonuses(
    *,
    bill: Bill,
    created_by=None,
    comment: str = "",
) -> BonusPaymentResult:
    """Apply the allowed bonus amount and close only when it covers the bill."""

    try:
        bill = Bill.objects.select_for_update().prefetch_related(
            "items",
            "payments",
            "bill_orders",
        ).get(
            id=bill.id,
            partner_id=bill.partner_id,
        )
    except Bill.DoesNotExist as exc:
        raise BillingServiceError("Счёт не найден в этом заведении.") from exc

    if bill.status == Bill.Status.CANCELED:
        raise BillingServiceError("Нельзя оплатить бонусами отменённый счёт.")
    if bill.status == Bill.Status.PAID:
        raise BillingServiceError("Счёт уже полностью оплачен.")

    order_ids = list(
        BillOrder.objects.filter(
            partner_id=bill.partner_id,
            bill_id=bill.id,
        )
        .order_by("order_id")
        .values_list("order_id", flat=True)
    )
    locked_orders = list(
        Order.objects.select_for_update()
        .filter(partner_id=bill.partner_id, id__in=order_ids)
        .order_by("id")
    )
    if len(locked_orders) != len(order_ids):
        raise BillingServiceError("Не все заказы счёта принадлежат этому заведению.")

    if bill.primary_guest_id is None:
        raise BillingServiceError("Оплата бонусами доступна только для персонального счёта.")
    try:
        primary_guest = GuestProfile.objects.select_for_update().get(
            id=bill.primary_guest_id,
            partner_id=bill.partner_id,
        )
    except GuestProfile.DoesNotExist as exc:
        raise BillingServiceError("Гость счёта не найден в этом заведении.") from exc
    bill.primary_guest = primary_guest
    if any(order.guest_id != primary_guest.id for order in locked_orders):
        raise BillingServiceError("Заказы персонального счёта принадлежат разным гостям.")
    try:
        assert_bonus_balance_reconciled(primary_guest)
    except BonusServiceError as exc:
        raise BillingServiceError(str(exc)) from exc

    recalculate_bill_totals(bill)
    if bill.bonus_spent_amount > 0:
        raise BillingServiceError("Бонусы по этому счёту уже были применены.")
    if bill.paid_amount > 0:
        raise BillingServiceError("Оплату бонусами нужно выбрать до первой денежной оплаты.")
    if bill.total_amount <= 0:
        raise BillingServiceError("В счёте нет положительной суммы для оплаты бонусами.")

    max_redeemable = get_redeemable_bonus_amount(
        partner_id=bill.partner_id,
        guest=primary_guest,
        purchase_total=bill.total_amount,
    )
    if max_redeemable <= 0:
        raise BillingServiceError("По этому счёту сейчас нечего оплачивать бонусами.")

    bill.bonus_spent_amount = max_redeemable
    recalculate_bill_totals(bill)
    BonusTransaction.objects.create(
        partner_id=bill.partner_id,
        guest=primary_guest,
        bill=bill,
        program=None,
        order=locked_orders[0] if len(locked_orders) == 1 else None,
        transaction_type=BonusTransaction.TransactionType.REDEMPTION,
        amount=max_redeemable,
        source_type="bill",
        source_id=str(bill.id),
        idempotency_key=f"bill:{bill.id}:redemption",
        comment=(
            f"Bill #{bill.public_id} bonus payment"
            f"{f' by {created_by.username}' if created_by else ''}."
        ),
    )
    try:
        sync_bonus_balance_from_ledger(primary_guest)
    except BonusServiceError as exc:
        raise BillingServiceError(str(exc)) from exc

    remaining_amount = bill.total_amount.quantize(Decimal("0.01"))
    payment = None
    if remaining_amount == Decimal("0.00"):
        now = timezone.now()
        payment = Payment.objects.create(
            partner_id=bill.partner_id,
            bill=bill,
            status=Payment.Status.PAID,
            method=Payment.Method.BONUSES,
            amount=max_redeemable,
            comment=comment or "Paid fully with bonus balance.",
            paid_at=now,
            created_by=created_by,
        )
        bill.status = Bill.Status.PAID
        bill.issued_at = bill.issued_at or now
        bill.closed_at = now
        bill.save(update_fields=["status", "issued_at", "closed_at", "updated_at"])
        bill = Bill.objects.prefetch_related(
            "items",
            "payments",
            "bill_orders",
        ).get(id=bill.id)
        _process_post_payment_updates(bill)
    elif bill.status == Bill.Status.DRAFT:
        bill.status = Bill.Status.ISSUED
        bill.issued_at = timezone.now()
        bill.save(update_fields=["status", "issued_at", "updated_at"])

    return BonusPaymentResult(
        bill=bill,
        bonus_spent_amount=max_redeemable,
        remaining_amount=remaining_amount,
        payment=payment,
    )


@transaction.atomic
def redeem_bonus_for_bill(
    *,
    bill: Bill,
    created_by=None,
    amount: Decimal | str | None = None,
) -> Decimal:
    bill = Bill.objects.select_for_update().prefetch_related(
        "items",
        "payments",
        "bill_orders",
    ).get(
        id=bill.id,
        partner_id=bill.partner_id,
    )
    if bill.primary_guest_id is None:
        raise BillingServiceError("Списание бонусов пока доступно только для персонального счёта.")
    try:
        primary_guest = GuestProfile.objects.select_for_update().get(
            id=bill.primary_guest_id,
            partner_id=bill.partner_id,
        )
    except GuestProfile.DoesNotExist as exc:
        raise BillingServiceError("Гость счёта не найден в этом заведении.") from exc
    bill.primary_guest = primary_guest
    try:
        assert_bonus_balance_reconciled(primary_guest)
    except BonusServiceError as exc:
        raise BillingServiceError(str(exc)) from exc
    recalculate_bill_totals(bill)

    if bill.status in {Bill.Status.CANCELED, Bill.Status.PAID}:
        raise BillingServiceError("Нельзя списать бонусы по закрытому счёту.")
    if bill.bonus_spent_amount > 0:
        raise BillingServiceError("Бонусы по этому счёту уже были списаны.")
    if bill.paid_amount > 0:
        raise BillingServiceError("Списание бонусов нужно делать до первой оплаты по счёту.")

    max_redeemable = get_redeemable_bonus_amount(
        partner_id=bill.partner_id,
        guest=primary_guest,
        purchase_total=bill.total_amount,
    )
    if max_redeemable <= 0:
        raise BillingServiceError("По этому счёту сейчас нечего списывать бонусами.")

    redeem_amount = max_redeemable
    if amount is not None:
        redeem_amount = Decimal(amount).quantize(Decimal("0.01"))
        if redeem_amount <= 0:
            raise BillingServiceError("Сумма списания бонусов должна быть больше нуля.")
        if redeem_amount > max_redeemable:
            raise BillingServiceError("Сумма списания превышает доступный лимит по бонусам.")

    bill.bonus_spent_amount = redeem_amount
    recalculate_bill_totals(bill)

    BonusTransaction.objects.create(
        partner_id=bill.partner_id,
        guest=primary_guest,
        bill=bill,
        program=None,
        order=bill.bill_orders.first().order if bill.bill_orders.count() == 1 else None,
        transaction_type=BonusTransaction.TransactionType.REDEMPTION,
        amount=redeem_amount,
        source_type="bill",
        source_id=str(bill.id),
        idempotency_key=f"bill:{bill.id}:redemption",
        comment=(
            f"Bill #{bill.public_id} redemption"
            f"{f' by {created_by.username}' if created_by else ''}."
        ),
    )
    try:
        sync_bonus_balance_from_ledger(primary_guest)
    except BonusServiceError as exc:
        raise BillingServiceError(str(exc)) from exc
    return redeem_amount


def _process_post_payment_updates(bill: Bill) -> None:
    if bill.status != Bill.Status.PAID:
        return

    _sync_orders_after_bill_paid(bill)
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


def _sync_orders_after_bill_paid(bill: Bill) -> int:
    paid_payments = list(bill.payments.filter(status=Payment.Status.PAID))
    order_payment_method = _derive_order_payment_method(
        paid_payments,
        bonus_spent_amount=bill.bonus_spent_amount,
    )
    order_ids = list(bill.bill_orders.values_list("order_id", flat=True))
    orders = list(
        Order.objects.select_for_update()
        .select_related("guest")
        .filter(id__in=order_ids, partner_id=bill.partner_id)
        .exclude(status=Order.Status.CANCELED)
        .order_by("id")
    )
    bonus_shares = _bill_bonus_share_by_order(bill)
    updated_count = 0
    now = timezone.now()
    for order in orders:
        was_unpaid = order.paid_at is None
        if order_payment_method and order.payment_method != order_payment_method:
            order.payment_method = order_payment_method
            order.save(update_fields=["payment_method", "updated_at"])

        if order.status in {Order.Status.READY, Order.Status.DELIVERING}:
            transition_order_status(
                order=order,
                to_status=Order.Status.COMPLETED,
                actor_user=None,
                note=f"Completed after payment for bill #{bill.public_id}.",
            )
            order.refresh_from_db()

        if order.paid_at is None:
            order.paid_at = now
            order.save(update_fields=["paid_at", "updated_at"])
        if was_unpaid:
            try:
                apply_bonus_programs(
                    partner_id=bill.partner_id,
                    event=BonusProgram.TriggerEvent.ORDER_COMPLETED,
                    guest=order.guest,
                    order=order,
                    purchase_total=order.total_amount,
                    bonus_paid_amount=bonus_shares.get(order.id, Decimal("0.00")),
                    source_type="order_paid",
                    source_id=str(order.id),
                    comment=f"Paid order bonus for order #{order.public_id}.",
                )
            except BonusServiceError as exc:
                raise BillingServiceError(str(exc)) from exc
        updated_count += 1
    return updated_count


def _bill_bonus_share_by_order(bill: Bill) -> dict:
    """Split the bill's bonus payment between its orders (PQ-030).

    Shares are proportional to each order's active lines in this bill; the
    rounding remainder goes to the last order, so the shares always add up to
    ``bill.bonus_spent_amount``.
    """

    bonus_spent = Decimal(bill.bonus_spent_amount).quantize(Decimal("0.01"))
    if bonus_spent <= 0:
        return {}
    line_totals: dict = {}
    for item in bill.items.filter(is_active=True).order_by("order_id"):
        line_totals[item.order_id] = line_totals.get(item.order_id, Decimal("0.00")) + Decimal(
            item.line_total
        )
    order_ids = sorted((order_id for order_id, total in line_totals.items() if total > 0), key=str)
    lines_sum = sum((line_totals[order_id] for order_id in order_ids), Decimal("0.00"))
    if lines_sum <= 0:
        return {}
    shares: dict = {}
    allocated = Decimal("0.00")
    for order_id in order_ids[:-1]:
        share = (bonus_spent * line_totals[order_id] / lines_sum).quantize(Decimal("0.01"))
        shares[order_id] = share
        allocated += share
    shares[order_ids[-1]] = bonus_spent - allocated
    return shares


def _derive_order_payment_method(
    paid_payments: list[Payment],
    *,
    bonus_spent_amount: Decimal = Decimal("0.00"),
) -> str | None:
    method_map = {
        Payment.Method.CASH: Order.PaymentMethod.CASH,
        Payment.Method.TERMINAL: Order.PaymentMethod.TERMINAL,
        Payment.Method.MIXED: Order.PaymentMethod.MIXED,
        Payment.Method.BONUSES: Order.PaymentMethod.BONUSES,
    }
    mapped_methods = {
        method_map[payment.method]
        for payment in paid_payments
        if payment.method in method_map
    }
    if bonus_spent_amount > 0 and any(
        payment.method != Payment.Method.BONUSES for payment in paid_payments
    ):
        return Order.PaymentMethod.MIXED
    if len(mapped_methods) > 1:
        return Order.PaymentMethod.MIXED
    if len(mapped_methods) == 1:
        return next(iter(mapped_methods))
    return None


@transaction.atomic
def mark_billing_request_processed(billing_request: BillingRequest) -> BillingRequest:
    try:
        billing_request = BillingRequest.objects.select_for_update().get(
            id=billing_request.id,
            partner_id=billing_request.partner_id,
        )
    except BillingRequest.DoesNotExist as exc:
        raise BillingServiceError("Запрос счёта не найден в этом заведении.") from exc
    if billing_request.status == BillingRequest.Status.CANCELED:
        raise BillingServiceError("Нельзя обработать уже отменённый запрос счёта.")
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
        raise BillingServiceError("Запрос счёта не найден в этом заведении.") from exc
    if billing_request.status == BillingRequest.Status.PROCESSED:
        raise BillingServiceError("Нельзя отменить уже обработанный запрос счёта.")
    if billing_request.status != BillingRequest.Status.CANCELED:
        billing_request.status = BillingRequest.Status.CANCELED
        billing_request.processed_at = timezone.now()
        billing_request.save(update_fields=["status", "processed_at", "updated_at"])
    return billing_request


# --- Bill corrections (B3.2 part A, PQ-028) ---------------------------------------
#
# Lock order: Bill -> BillItem -> GuestProfile (void with bonuses) -> Order
# (cancel_order). Every change is recorded as an append-only BillAdjustment.


def user_can_adjust_bills(user, partner_id) -> bool:
    """Return whether ``user`` may change or void bills of ``partner_id``.

    The venue owner always may; other staff only when the owner granted
    ``EmployeeProfile.can_adjust_bills``.
    """

    if user is None or not getattr(user, "is_active", False):
        return False
    if user.is_superuser:
        return True
    if str(user.partner_id) != str(partner_id):
        return False
    if user.role == User.Role.OWNER:
        return True
    if AdminAccessProfile.objects.filter(
        user=user,
        partner_id=partner_id,
        is_partner_owner=True,
        can_access_admin=True,
        is_active=True,
    ).exists():
        return True
    return EmployeeProfile.objects.filter(
        user=user,
        partner_id=partner_id,
        is_active=True,
        can_adjust_bills=True,
    ).exists()


def _validate_adjustment_reason(reason_code: str) -> None:
    if reason_code not in BillAdjustment.ReasonCode.values:
        raise BillingServiceError("Укажите причину изменения счёта.")


def _lock_bill_for_change(bill: Bill, actor) -> Bill:
    try:
        locked_bill = Bill.objects.select_for_update().get(
            id=bill.id,
            partner_id=bill.partner_id,
        )
    except Bill.DoesNotExist as exc:
        raise BillingServiceError("Счёт не найден в этом заведении.") from exc
    if not user_can_adjust_bills(actor, locked_bill.partner_id):
        raise BillingServiceError("Нет права изменять счета. Его выдаёт владелец заведения.")
    if locked_bill.status == Bill.Status.PAID:
        raise BillingServiceError("Оплаченный счёт изменить нельзя.")
    if locked_bill.status == Bill.Status.CANCELED:
        raise BillingServiceError("Счёт уже аннулирован.")
    return locked_bill


def _release_bill_lines(*, bill: Bill, items: list[BillItem], actor) -> None:
    now = timezone.now()
    for item in items:
        item.is_active = False
        item.removed_at = now
        item.removed_by = actor
        item.updated_at = now
    BillItem.objects.bulk_update(items, ["is_active", "removed_at", "removed_by", "updated_at"])
    affected_order_ids = {item.order_id for item in items}
    still_billed = set(
        BillItem.objects.filter(
            bill_id=bill.id,
            is_active=True,
            order_id__in=affected_order_ids,
        ).values_list("order_id", flat=True)
    )
    BillOrder.objects.filter(
        bill_id=bill.id,
        order_id__in=affected_order_ids - still_billed,
    ).delete()


@transaction.atomic
def remove_bill_lines(
    *,
    bill: Bill,
    order_item_ids: list,
    actor,
    reason_code: str,
    reason_text: str = "",
    kind: str = BillAdjustment.Kind.REMOVE_LINE,
) -> BillAdjustment:
    """Remove lines from an unpaid bill; the total may not drop below the paid amount."""

    _validate_adjustment_reason(reason_code)
    bill = _lock_bill_for_change(bill, actor)
    if bill.bonus_spent_amount > 0:
        raise BillingServiceError(
            "По счёту применены бонусы: его нельзя менять по позициям, только аннулировать."
        )

    requested_ids = list(dict.fromkeys(str(item_id) for item_id in order_item_ids))
    if not requested_ids:
        raise BillingServiceError("Выберите позиции, которые нужно убрать из счёта.")
    items = list(
        BillItem.objects.select_for_update()
        .filter(
            partner_id=bill.partner_id,
            bill_id=bill.id,
            is_active=True,
            order_item_id__in=requested_ids,
        )
        .order_by("id")
    )
    if len(items) != len(requested_ids):
        raise BillingServiceError("Часть позиций не найдена среди активных позиций счёта.")

    bill = Bill.objects.prefetch_related("items", "payments").get(id=bill.id)
    _subtotal, total_before, paid_amount = _compute_bill_totals(bill)
    removed_total = sum((item.line_total for item in items), Decimal("0.00"))
    total_after = max(Decimal("0.00"), total_before - removed_total).quantize(Decimal("0.01"))
    if total_after < paid_amount:
        raise BillingServiceError(
            f"Нельзя уменьшить счёт ниже уже оплаченной суммы ({paid_amount} грн)."
        )

    _release_bill_lines(bill=bill, items=items, actor=actor)
    bill = Bill.objects.prefetch_related("items", "payments", "bill_orders").get(id=bill.id)
    recalculate_bill_totals(bill)
    adjustment = BillAdjustment.objects.create(
        partner_id=bill.partner_id,
        bill=bill,
        kind=kind,
        actor=actor,
        reason_code=reason_code,
        reason_text=reason_text[:255],
        total_before=total_before,
        total_after=bill.total_amount,
        payload={
            "order_item_ids": requested_ids,
            "order_ids": sorted({str(item.order_id) for item in items}),
        },
    )

    if bill.paid_amount > 0 and bill.paid_amount == bill.total_amount:
        # The remaining lines are already covered by money payments.
        bill.status = Bill.Status.PAID
        bill.closed_at = timezone.now()
        bill.save(update_fields=["status", "closed_at", "updated_at"])
        _process_post_payment_updates(bill)
    return adjustment


@transaction.atomic
def void_bill(*, bill: Bill, actor, reason_code: str, reason_text: str = "") -> BillAdjustment:
    """Void an unpaid bill, release its orders and return applied bonuses."""

    _validate_adjustment_reason(reason_code)
    bill = _lock_bill_for_change(bill, actor)
    bill = Bill.objects.prefetch_related("items", "payments").get(id=bill.id)
    _subtotal, total_before, paid_amount = _compute_bill_totals(bill)
    if paid_amount > 0:
        raise BillingServiceError("По счёту уже есть оплата деньгами — аннулировать его нельзя.")

    items = list(
        BillItem.objects.select_for_update()
        .filter(partner_id=bill.partner_id, bill_id=bill.id, is_active=True)
        .order_by("id")
    )
    bonus_returned = Decimal(bill.bonus_spent_amount).quantize(Decimal("0.01"))
    if bonus_returned > 0:
        _return_bill_bonuses(bill=bill, amount=bonus_returned, actor=actor)

    _release_bill_lines(bill=bill, items=items, actor=actor)
    bill.status = Bill.Status.CANCELED
    bill.closed_at = timezone.now()
    bill.save(update_fields=["status", "closed_at", "updated_at"])

    reopened_requests = list(
        BillingRequest.objects.select_for_update()
        .filter(
            partner_id=bill.partner_id,
            bill_id=bill.id,
            status__in=[BillingRequest.Status.OPEN, BillingRequest.Status.AUTO_PREPARED],
        )
        .order_by("id")
    )
    for billing_request in reopened_requests:
        billing_request.status = BillingRequest.Status.OPEN
        billing_request.bill = None
        billing_request.note = f"Счёт #{bill.public_id} аннулирован."
        billing_request.save(update_fields=["status", "bill", "note", "updated_at"])

    return BillAdjustment.objects.create(
        partner_id=bill.partner_id,
        bill=bill,
        kind=BillAdjustment.Kind.VOID,
        actor=actor,
        reason_code=reason_code,
        reason_text=reason_text[:255],
        total_before=total_before,
        total_after=Decimal("0.00"),
        payload={
            "order_item_ids": [str(item.order_item_id) for item in items],
            "order_ids": sorted({str(item.order_id) for item in items}),
            "bonus_returned": str(bonus_returned),
            "reopened_request_ids": [str(request.id) for request in reopened_requests],
        },
    )


def _return_bill_bonuses(*, bill: Bill, amount: Decimal, actor) -> None:
    if bill.primary_guest_id is None:
        raise BillingServiceError("У счёта с бонусами нет гостя — вернуть бонусы некому.")
    guest = GuestProfile.objects.select_for_update().get(
        id=bill.primary_guest_id,
        partner_id=bill.partner_id,
    )
    try:
        assert_bonus_balance_reconciled(guest)
    except BonusServiceError as exc:
        raise BillingServiceError(str(exc)) from exc
    actor_label = getattr(actor, "username", "")
    BonusTransaction.objects.create(
        partner_id=bill.partner_id,
        guest=guest,
        bill=bill,
        transaction_type=BonusTransaction.TransactionType.REVERSAL,
        amount=amount,
        source_type="bill_void",
        source_id=str(bill.id),
        idempotency_key=f"bill:{bill.id}:redemption_reversal",
        comment=(
            f"Bill #{bill.public_id} voided, bonuses returned"
            f"{f' by {actor_label}' if actor_label else ''}."
        ),
    )
    sync_bonus_balance_from_ledger(guest)


@transaction.atomic
def cancel_order(
    *,
    order: Order,
    actor=None,
    reason_code: str = "",
    reason_text: str = "",
    note: str = "",
) -> Order:
    """Cancel an order; if it is in an open bill, remove its lines first.

    Removing the lines of a billed order is a bill change: it needs the bill
    permission and a reason. Orders in a paid bill cannot be cancelled.
    """

    active_items = list(
        BillItem.objects.filter(
            partner_id=order.partner_id,
            order_id=order.id,
            is_active=True,
        ).select_related("bill")
    )
    bills = {item.bill_id: item.bill for item in active_items}
    for bill in sorted(bills.values(), key=lambda value: str(value.id)):
        if bill.status == Bill.Status.PAID:
            raise BillingServiceError(
                f"Заказ уже оплачен в счёте #{bill.public_id}, отменить его нельзя."
            )
        if not reason_code:
            raise BillingServiceError(
                f"Заказ уже в счёте #{bill.public_id}. Отмените его через карточку счёта "
                "с указанием причины."
            )
        remove_bill_lines(
            bill=bill,
            order_item_ids=[item.order_item_id for item in active_items if item.bill_id == bill.id],
            actor=actor,
            reason_code=reason_code,
            reason_text=reason_text,
            kind=BillAdjustment.Kind.ORDER_CANCELED,
        )
    return transition_order_status(
        order=order,
        to_status=Order.Status.CANCELED,
        actor_user=actor,
        note=note or reason_text,
    )


ADJUSTMENT_COMMENT_WINDOW = timedelta(minutes=30)


@transaction.atomic
def annotate_bill_adjustment(*, adjustment_id, partner_id, actor, text: str) -> BillAdjustment:
    """Let the author add the optional comment to a just-made bill change.

    The record stays append-only otherwise: only an empty comment of the
    author's own adjustment can be filled, within a short window.
    """

    comment = (text or "").strip()
    if not comment:
        raise BillingServiceError("Комментарий пустой.")
    try:
        adjustment = BillAdjustment.objects.select_for_update().get(
            id=adjustment_id,
            partner_id=partner_id,
        )
    except BillAdjustment.DoesNotExist as exc:
        raise BillingServiceError("Изменение счёта не найдено.") from exc
    if actor is None or adjustment.actor_id != actor.id:
        raise BillingServiceError("Комментарий может добавить только автор изменения.")
    if adjustment.reason_text:
        raise BillingServiceError("Комментарий к этому изменению уже добавлен.")
    if timezone.now() - adjustment.created_at > ADJUSTMENT_COMMENT_WINDOW:
        raise BillingServiceError("Время на добавление комментария истекло.")
    adjustment.reason_text = comment[:255]
    adjustment.save(update_fields=["reason_text", "updated_at"])
    return adjustment
