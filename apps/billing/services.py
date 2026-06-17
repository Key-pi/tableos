from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from itertools import groupby

from django.db import transaction
from django.utils import timezone

from apps.billing.models import (
    Bill,
    BillingRequest,
    BillItem,
    BillOrder,
    Payment,
)
from apps.bonuses.models import BonusTransaction
from apps.bonuses.services import get_redeemable_bonus_amount
from apps.notifications.services import notify_staff_about_billing_request
from apps.orders.models import Cart, Order, OrderItem
from apps.orders.services import transition_order_status
from apps.partners.models import PartnerBotSettings
from apps.tables.models import TableSession
from apps.tables.services import close_table_session, get_active_table_session


class BillingServiceError(Exception):
    """Raised when a bill or payment operation violates billing rules."""


@dataclass(frozen=True, slots=True)
class BillingRequestResult:
    request: BillingRequest
    bill: Bill | None
    notified_count: int


def get_bill_remaining_amount(bill: Bill) -> Decimal:
    refreshed_bill = Bill.objects.prefetch_related("items", "payments").get(id=bill.id)
    recalculate_bill_totals(refreshed_bill)
    return (refreshed_bill.total_amount - refreshed_bill.paid_amount).quantize(Decimal("0.01"))


def get_bill_context_label(bill: Bill) -> str:
    if bill.table_id is not None:
        return f"стол #{bill.table.number}"
    if bill.primary_guest_id is not None:
        return f"гость {bill.primary_guest.customer_code}"
    return "заказ без привязки к столу"


def get_table_billable_orders(
    *,
    partner_id,
    table_id,
) -> list[Order]:
    allocated_order_ids = set(
        BillItem.objects.filter(
            partner_id=partner_id,
            bill__status__in=[
                Bill.Status.DRAFT,
                Bill.Status.ISSUED,
                Bill.Status.PARTIALLY_PAID,
                Bill.Status.PAID,
            ],
        ).values_list("order_id", flat=True)
    )
    return list(
        Order.objects.select_related("table", "guest", "guest__telegram_account")
        .prefetch_related("items")
        .filter(
            partner_id=partner_id,
            table_id=table_id,
        )
        .exclude(status=Order.Status.CANCELED)
        .exclude(id__in=allocated_order_ids)
        .order_by("guest_id", "created_at")
    )


def attach_orders_to_bill(
    *,
    bill: Bill,
    order_ids: list[str],
) -> Bill:
    if bill.status in {Bill.Status.CANCELED, Bill.Status.PAID}:
        raise BillingServiceError("Нельзя добавлять заказы в закрытый счёт.")
    if not order_ids:
        return bill

    requested_order_ids = {str(order_id) for order_id in order_ids}
    existing_order_ids = set(bill.bill_orders.values_list("order_id", flat=True))
    requested_orders = list(
        Order.objects.select_related("table", "guest")
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

    if any(order.status == Order.Status.CANCELED for order in orders):
        raise BillingServiceError("Отменённые заказы нельзя включать в счёт.")

    target_order_item_ids = OrderItem.objects.filter(
        order_id__in=[order.id for order in orders]
    ).values("id")
    allocated_order_item_ids = set(
        BillItem.objects.filter(
            partner_id=bill.partner_id,
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
            for order_item in order.items.all()
        ]
    )
    bill = Bill.objects.prefetch_related("items", "payments", "bill_orders").get(id=bill.id)
    return recalculate_bill_totals(bill)


def recalculate_bill_totals(bill: Bill) -> Bill:
    subtotal = sum((item.line_total for item in bill.items.all()), Decimal("0.00"))
    paid_amount = sum(
        (
            payment.amount
            for payment in bill.payments.filter(status=Payment.Status.PAID)
        ),
        Decimal("0.00"),
    )
    bill.subtotal_amount = subtotal
    bill.total_amount = max(
        Decimal("0.00"),
        subtotal - bill.discount_amount - bill.bonus_spent_amount,
    ).quantize(Decimal("0.01"))
    bill.paid_amount = paid_amount.quantize(Decimal("0.01"))
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
    orders = list(
        Order.objects.select_related("table", "guest")
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

    allocated_order_item_ids = set(
        BillItem.objects.filter(
            partner_id=partner_id,
            order_item_id__in=OrderItem.objects.filter(order_id__in=requested_order_ids).values("id"),
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
        for order_item in order.items.all():
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
) -> Bill:
    orders = get_table_billable_orders(
        partner_id=partner_id,
        table_id=table_id,
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
) -> list[Bill]:
    orders = get_table_billable_orders(
        partner_id=partner_id,
        table_id=table_id,
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
) -> Bill:
    open_bill = (
        Bill.objects.filter(
            partner_id=partner_id,
            table_id=table_id,
            primary_guest_id=guest_id,
            kind=Bill.Kind.PERSONAL,
            status__in=[Bill.Status.DRAFT, Bill.Status.ISSUED, Bill.Status.PARTIALLY_PAID],
        )
        .order_by("-created_at")
        .first()
    )
    guest_orders = [
        order
        for order in get_table_billable_orders(partner_id=partner_id, table_id=table_id)
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
) -> Bill:
    open_bill = (
        Bill.objects.filter(
            partner_id=partner_id,
            table_id=table_id,
            kind=Bill.Kind.SHARED,
            status__in=[Bill.Status.DRAFT, Bill.Status.ISSUED, Bill.Status.PARTIALLY_PAID],
        )
        .order_by("-created_at")
        .first()
    )
    orders = get_table_billable_orders(partner_id=partner_id, table_id=table_id)
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
        )
        status = BillingRequest.Status.AUTO_PREPARED
        note = "Personal draft bill prepared automatically."
    elif request_type == BillingRequest.RequestType.SHARED:
        bill = get_or_create_shared_bill_for_table(
            partner_id=partner_id,
            table_id=table_session.table_id,
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
    if bill.status == Bill.Status.CANCELED:
        raise BillingServiceError("Нельзя принять оплату по отменённому счёту.")
    if bill.status == Bill.Status.PAID:
        raise BillingServiceError("Счёт уже полностью оплачен.")

    amount_value = Decimal(amount).quantize(Decimal("0.01"))
    if amount_value <= 0:
        raise BillingServiceError("Сумма оплаты должна быть больше нуля.")
    if method not in Payment.Method.values:
        raise BillingServiceError("Неизвестный способ оплаты.")

    bill = Bill.objects.select_for_update().prefetch_related("items", "payments").get(id=bill.id)
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
def redeem_bonus_for_bill(
    *,
    bill: Bill,
    created_by=None,
    amount: Decimal | str | None = None,
) -> Decimal:
    bill = Bill.objects.select_for_update().select_related("primary_guest").prefetch_related(
        "items",
        "payments",
        "bill_orders",
    ).get(id=bill.id)
    recalculate_bill_totals(bill)

    if bill.status in {Bill.Status.CANCELED, Bill.Status.PAID}:
        raise BillingServiceError("Нельзя списать бонусы по закрытому счёту.")
    if bill.primary_guest_id is None:
        raise BillingServiceError("Списание бонусов пока доступно только для персонального счёта.")
    if bill.bonus_spent_amount > 0:
        raise BillingServiceError("Бонусы по этому счёту уже были списаны.")
    if bill.paid_amount > 0:
        raise BillingServiceError("Списание бонусов нужно делать до первой оплаты по счёту.")

    max_redeemable = get_redeemable_bonus_amount(
        partner_id=bill.partner_id,
        guest=bill.primary_guest,
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
        guest=bill.primary_guest,
        program=None,
        order=bill.bill_orders.first().order if bill.bill_orders.count() == 1 else None,
        transaction_type=BonusTransaction.TransactionType.REDEMPTION,
        amount=redeem_amount,
        comment=(
            f"Bill #{bill.public_id} redemption"
            f"{f' by {created_by.username}' if created_by else ''}."
        ),
    )
    bill.primary_guest.loyalty_balance = (
        Decimal(bill.primary_guest.loyalty_balance) - redeem_amount
    ).quantize(Decimal("0.01"))
    bill.primary_guest.save(update_fields=["loyalty_balance", "updated_at"])
    return redeem_amount


def _process_post_payment_updates(bill: Bill) -> None:
    if bill.status != Bill.Status.PAID:
        return

    _sync_orders_after_bill_paid(bill)
    _mark_related_billing_requests_processed(bill)
    if bill.table_id is None:
        return
    if _partner_auto_closes_table_session_after_payment(partner_id=bill.partner_id):
        _close_table_sessions_if_fully_settled(
            partner_id=bill.partner_id,
            table_id=bill.table_id,
        )


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
    return BillingRequest.objects.filter(
        partner_id=bill.partner_id,
        bill=bill,
        status__in=[
            BillingRequest.Status.OPEN,
            BillingRequest.Status.AUTO_PREPARED,
        ],
    ).update(
        status=BillingRequest.Status.PROCESSED,
        processed_at=timezone.now(),
    )


def _close_table_sessions_if_fully_settled(*, partner_id, table_id) -> int:
    has_open_bills = Bill.objects.filter(
        partner_id=partner_id,
        table_id=table_id,
        status__in=[
            Bill.Status.DRAFT,
            Bill.Status.ISSUED,
            Bill.Status.PARTIALLY_PAID,
        ],
    ).exists()
    if has_open_bills:
        return 0

    has_active_carts = Cart.objects.filter(
        partner_id=partner_id,
        table_session__table_id=table_id,
        status=Cart.Status.ACTIVE,
    ).exists()
    if has_active_carts:
        return 0

    settled_order_ids = BillOrder.objects.filter(
        partner_id=partner_id,
        bill__table_id=table_id,
        bill__status=Bill.Status.PAID,
    ).values_list("order_id", flat=True)
    active_orders = Order.objects.filter(
        partner_id=partner_id,
        table_id=table_id,
    ).exclude(
        status=Order.Status.CANCELED,
    )
    has_unsettled_orders = active_orders.exclude(
        id__in=settled_order_ids,
    ).exists()
    if has_unsettled_orders:
        return 0

    has_unfinished_orders = active_orders.exclude(status=Order.Status.COMPLETED).exists()
    if has_unfinished_orders:
        return 0

    active_sessions = list(
        TableSession.objects.filter(
            partner_id=partner_id,
            table_id=table_id,
            status=TableSession.Status.ACTIVE,
        )
    )
    for session in active_sessions:
        close_table_session(session)

    BillingRequest.objects.filter(
        partner_id=partner_id,
        table_id=table_id,
        status__in=[
            BillingRequest.Status.OPEN,
            BillingRequest.Status.AUTO_PREPARED,
        ],
    ).update(
        status=BillingRequest.Status.PROCESSED,
        processed_at=timezone.now(),
    )
    return len(active_sessions)


def _sync_orders_after_bill_paid(bill: Bill) -> int:
    paid_payments = list(bill.payments.filter(status=Payment.Status.PAID))
    order_payment_method = _derive_order_payment_method(paid_payments)
    order_ids = list(bill.bill_orders.values_list("order_id", flat=True))
    orders = list(
        Order.objects.filter(id__in=order_ids).exclude(status=Order.Status.CANCELED)
    )
    updated_count = 0
    now = timezone.now()
    for order in orders:
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
        updated_count += 1
    return updated_count


def _derive_order_payment_method(paid_payments: list[Payment]) -> str | None:
    method_map = {
        Payment.Method.CASH: Order.PaymentMethod.CASH,
        Payment.Method.TERMINAL: Order.PaymentMethod.TERMINAL,
    }
    mapped_methods = {
        method_map[payment.method]
        for payment in paid_payments
        if payment.method in method_map
    }
    if len(mapped_methods) == 1:
        return next(iter(mapped_methods))
    return None
def mark_billing_request_processed(billing_request: BillingRequest) -> BillingRequest:
    if billing_request.status == BillingRequest.Status.CANCELED:
        raise BillingServiceError("Нельзя обработать уже отменённый запрос счёта.")
    if billing_request.status != BillingRequest.Status.PROCESSED:
        billing_request.status = BillingRequest.Status.PROCESSED
        billing_request.processed_at = timezone.now()
        billing_request.save(update_fields=["status", "processed_at", "updated_at"])
    return billing_request


def cancel_billing_request(billing_request: BillingRequest) -> BillingRequest:
    if billing_request.status == BillingRequest.Status.PROCESSED:
        raise BillingServiceError("Нельзя отменить уже обработанный запрос счёта.")
    if billing_request.status != BillingRequest.Status.CANCELED:
        billing_request.status = BillingRequest.Status.CANCELED
        billing_request.processed_at = timezone.now()
        billing_request.save(update_fields=["status", "processed_at", "updated_at"])
    return billing_request
