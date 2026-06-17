from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as dt_timezone
from decimal import Decimal
from math import ceil
from zoneinfo import ZoneInfo

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.analytics.models import PartnerDailyMetric
from apps.billing.models import Bill, BillingRequest, Payment
from apps.billing.services import get_bill_context_label
from apps.bonuses.models import WalkInSale
from apps.orders.models import Order
from apps.partners.models import Partner
from apps.tables.models import Table, TableSession
from apps.users.models import GuestProfile


@dataclass(frozen=True, slots=True)
class DailyOperationsReport:
    partner_name: str
    local_date_label: str
    total_revenue: Decimal
    bill_revenue: Decimal
    walk_in_revenue: Decimal
    paid_bills_count: int
    walk_in_sales_count: int
    created_orders_count: int
    open_orders_count: int
    open_bills_count: int
    payment_method_totals: dict[str, Decimal]
    payment_method_counts: dict[str, int]
    staff_breakdown: list[dict]


@dataclass(frozen=True, slots=True)
class ReportPage:
    section: str
    page: int
    total_pages: int
    total_count: int
    items: list[dict]


@dataclass(frozen=True, slots=True)
class DailyTableDetail:
    table_id: str
    table_number: int
    session_count: int
    active_session_count: int
    order_count: int
    paid_bill_count: int
    open_bill_count: int
    paid_total: Decimal
    open_total: Decimal
    sessions: list[dict]
    orders: list[dict]
    paid_bills: list[dict]
    open_bills: list[dict]


REPORT_PAGE_SIZE = 5


def _resolve_partner_day_bounds(*, partner_id, bucket_date=None):
    partner = Partner.objects.get(id=partner_id)
    now = timezone.now()
    partner_tz = ZoneInfo(partner.timezone)
    local_now = now.astimezone(partner_tz)
    target_date = bucket_date or local_now.date()
    day_start_local = datetime(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        tzinfo=partner_tz,
    )
    day_end_local = day_start_local.replace(hour=23, minute=59, second=59, microsecond=999999)
    day_start = day_start_local.astimezone(dt_timezone.utc)
    day_end = day_end_local.astimezone(dt_timezone.utc)
    return partner, day_start_local, day_start, day_end


def _paginate_items(*, section: str, items: list[dict], page: int) -> ReportPage:
    normalized_page = max(page, 1)
    total_count = len(items)
    total_pages = max(ceil(total_count / REPORT_PAGE_SIZE), 1)
    page = min(normalized_page, total_pages)
    start = (page - 1) * REPORT_PAGE_SIZE
    end = start + REPORT_PAGE_SIZE
    return ReportPage(
        section=section,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        items=items[start:end],
    )


def build_daily_operations_report(*, partner_id, bucket_date=None) -> DailyOperationsReport:
    partner, day_start_local, day_start, day_end = _resolve_partner_day_bounds(
        partner_id=partner_id,
        bucket_date=bucket_date,
    )

    paid_payments = Payment.objects.filter(
        partner_id=partner_id,
        status=Payment.Status.PAID,
        paid_at__gte=day_start,
        paid_at__lte=day_end,
    )
    bill_revenue = paid_payments.aggregate(
        total=Coalesce(Sum("amount"), Decimal("0.00")),
    )["total"]
    payment_method_totals = {
        method: paid_payments.filter(method=method).aggregate(
            total=Coalesce(Sum("amount"), Decimal("0.00")),
        )["total"]
        for method, _label in Payment.Method.choices
    }
    payment_method_counts = {
        method: paid_payments.filter(method=method).count()
        for method, _label in Payment.Method.choices
    }

    paid_bills_count = Bill.objects.filter(
        partner_id=partner_id,
        status=Bill.Status.PAID,
        closed_at__gte=day_start,
        closed_at__lte=day_end,
    ).count()
    walk_in_sales = WalkInSale.objects.filter(
        partner_id=partner_id,
        created_at__gte=day_start,
        created_at__lte=day_end,
    )
    walk_in_revenue = walk_in_sales.aggregate(
        total=Coalesce(Sum("amount"), Decimal("0.00")),
    )["total"]
    walk_in_sales_count = walk_in_sales.count()

    created_orders_count = Order.objects.filter(
        partner_id=partner_id,
        created_at__gte=day_start,
        created_at__lte=day_end,
    ).count()
    open_orders_count = Order.objects.filter(
        partner_id=partner_id,
        status__in=[
            Order.Status.NEW,
            Order.Status.ACCEPTED,
            Order.Status.PREPARING,
            Order.Status.READY,
            Order.Status.DELIVERING,
        ],
    ).count()
    open_bills_count = Bill.objects.filter(
        partner_id=partner_id,
        status__in=[
            Bill.Status.DRAFT,
            Bill.Status.ISSUED,
            Bill.Status.PARTIALLY_PAID,
        ],
    ).count()

    payment_staff_rows = list(
        paid_payments.exclude(created_by__isnull=True)
        .values("created_by__username", "created_by__first_name", "created_by__last_name")
        .annotate(
            payments_count=Count("id"),
            total_amount=Coalesce(Sum("amount"), Decimal("0.00")),
        )
        .order_by("-total_amount")
    )
    walk_in_staff_rows = list(
        walk_in_sales.exclude(created_by__isnull=True)
        .values("created_by__username", "created_by__first_name", "created_by__last_name")
        .annotate(
            sales_count=Count("id"),
            total_amount=Coalesce(Sum("amount"), Decimal("0.00")),
        )
        .order_by("-total_amount")
    )
    staff_breakdown_map: dict[str, dict] = {}

    for row in payment_staff_rows:
        label = _staff_label(row)
        staff_breakdown_map[label] = {
            "label": label,
            "payments_count": row["payments_count"],
            "walk_in_sales_count": 0,
            "total_amount": row["total_amount"],
        }

    for row in walk_in_staff_rows:
        label = _staff_label(row)
        entry = staff_breakdown_map.setdefault(
            label,
            {
                "label": label,
                "payments_count": 0,
                "walk_in_sales_count": 0,
                "total_amount": Decimal("0.00"),
            },
        )
        entry["walk_in_sales_count"] += row["sales_count"]
        entry["total_amount"] += row["total_amount"]

    staff_breakdown = sorted(
        staff_breakdown_map.values(),
        key=lambda item: item["total_amount"],
        reverse=True,
    )

    return DailyOperationsReport(
        partner_name=partner.name,
        local_date_label=day_start_local.strftime("%d.%m.%Y"),
        total_revenue=(bill_revenue + walk_in_revenue).quantize(Decimal("0.01")),
        bill_revenue=Decimal(bill_revenue).quantize(Decimal("0.01")),
        walk_in_revenue=Decimal(walk_in_revenue).quantize(Decimal("0.01")),
        paid_bills_count=paid_bills_count,
        walk_in_sales_count=walk_in_sales_count,
        created_orders_count=created_orders_count,
        open_orders_count=open_orders_count,
        open_bills_count=open_bills_count,
        payment_method_totals={
            key: Decimal(value).quantize(Decimal("0.01"))
            for key, value in payment_method_totals.items()
        },
        payment_method_counts=payment_method_counts,
        staff_breakdown=staff_breakdown,
    )


def refresh_partner_daily_metric(*, partner_id, bucket_date=None) -> PartnerDailyMetric:
    partner, day_start_local, day_start, day_end = _resolve_partner_day_bounds(
        partner_id=partner_id,
        bucket_date=bucket_date,
    )
    report = build_daily_operations_report(partner_id=partner_id, bucket_date=bucket_date)

    session_guest_ids = set(
        TableSession.objects.filter(
            partner_id=partner_id,
            started_at__gte=day_start,
            started_at__lte=day_end,
        ).values_list("guest_id", flat=True)
    )
    order_guest_ids = set(
        Order.objects.filter(
            partner_id=partner_id,
            created_at__gte=day_start,
            created_at__lte=day_end,
        ).values_list("guest_id", flat=True)
    )
    walk_in_guest_ids = set(
        WalkInSale.objects.filter(
            partner_id=partner_id,
            created_at__gte=day_start,
            created_at__lte=day_end,
            guest_id__isnull=False,
        ).values_list("guest_id", flat=True)
    )
    active_guest_ids = session_guest_ids | order_guest_ids | walk_in_guest_ids

    repeat_visits = 0
    if active_guest_ids:
        repeat_visits = GuestProfile.objects.filter(
            partner_id=partner_id,
            id__in=active_guest_ids,
            first_visit_at__lt=day_start,
            last_visit_at__gte=day_start,
            last_visit_at__lte=day_end,
        ).count()

    sales_count = report.paid_bills_count + report.walk_in_sales_count
    average_check = Decimal("0.00")
    if sales_count > 0:
        average_check = (report.total_revenue / sales_count).quantize(Decimal("0.01"))

    metric, _created = PartnerDailyMetric.objects.update_or_create(
        partner=partner,
        bucket_date=day_start_local.date(),
        defaults={
            "revenue": report.total_revenue,
            "orders_count": report.created_orders_count,
            "average_check": average_check,
            "active_guests": len(active_guest_ids),
            "repeat_visits": repeat_visits,
        },
    )
    return metric


def refresh_daily_partner_metrics() -> int:
    refreshed = 0
    active_partner_ids = Partner.objects.filter(status=Partner.Status.ACTIVE).values_list(
        "id",
        flat=True,
    )
    for partner_id in active_partner_ids:
        refresh_partner_daily_metric(partner_id=partner_id)
        refreshed += 1
    return refreshed


def build_daily_paid_bills_page(*, partner_id, page: int = 1) -> ReportPage:
    _partner, _day_start_local, day_start, day_end = _resolve_partner_day_bounds(
        partner_id=partner_id
    )
    bills = list(
        Bill.objects.filter(
            partner_id=partner_id,
            status=Bill.Status.PAID,
            closed_at__gte=day_start,
            closed_at__lte=day_end,
        )
        .select_related("table", "primary_guest", "primary_guest__telegram_account")
        .prefetch_related("payments", "items", "bill_orders")
        .order_by("-closed_at", "-updated_at")
    )
    items = [
        {
            "kind": "bill",
            "public_id": bill.public_id,
            "label": (
                f"{_format_dt(bill.closed_at)} • {get_bill_context_label(bill)} • "
                f"{bill.get_kind_display()} • {bill.total_amount} грн • "
                f"{_bill_payment_method_label(bill)}"
            ),
            "subtitle": (
                f"Позиций: {bill.items.count()} • заказов: {bill.bill_orders.count()} • "
                f"гость: {_guest_label(bill.primary_guest)}"
            ),
        }
        for bill in bills
    ]
    return _paginate_items(section="bills", items=items, page=page)


def build_daily_walk_in_sales_page(*, partner_id, page: int = 1) -> ReportPage:
    _partner, _day_start_local, day_start, day_end = _resolve_partner_day_bounds(
        partner_id=partner_id
    )
    sales = list(
        WalkInSale.objects.filter(
            partner_id=partner_id,
            created_at__gte=day_start,
            created_at__lte=day_end,
        )
        .select_related("guest", "guest__telegram_account", "created_by")
        .prefetch_related("items")
        .order_by("-created_at")
    )
    items = [
        {
            "kind": "walkin",
            "id": str(sale.id),
            "label": (
                f"{_format_dt(sale.created_at)} • {sale.loyalty_label} • "
                f"{sale.amount} грн • {_staff_user_label(sale.created_by)}"
            ),
            "subtitle": (
                f"Позиции: {sale.items.count()} • бонусов: {sale.bonus_awarded_amount} • "
                f"комментарий: {sale.comment or '—'}"
            ),
        }
        for sale in sales
    ]
    return _paginate_items(section="walkins", items=items, page=page)


def build_daily_tables_page(*, partner_id, page: int = 1) -> ReportPage:
    _partner, _day_start_local, day_start, day_end = _resolve_partner_day_bounds(
        partner_id=partner_id
    )
    tables = list(Table.objects.filter(partner_id=partner_id).order_by("number"))
    sessions = list(
        TableSession.objects.filter(
            partner_id=partner_id,
            started_at__gte=day_start,
            started_at__lte=day_end,
        ).select_related("table")
    )
    orders = list(
        Order.objects.filter(
            partner_id=partner_id,
            created_at__gte=day_start,
            created_at__lte=day_end,
        ).select_related("table")
    )
    paid_bills = list(
        Bill.objects.filter(
            partner_id=partner_id,
            status=Bill.Status.PAID,
            closed_at__gte=day_start,
            closed_at__lte=day_end,
        ).select_related("table")
    )
    open_bills = list(
        Bill.objects.filter(
            partner_id=partner_id,
            status__in=[Bill.Status.DRAFT, Bill.Status.ISSUED, Bill.Status.PARTIALLY_PAID],
        ).select_related("table")
    )
    table_rows: list[dict] = []
    for table in tables:
        table_sessions = [session for session in sessions if session.table_id == table.id]
        table_orders = [order for order in orders if order.table_id == table.id]
        table_paid_bills = [bill for bill in paid_bills if bill.table_id == table.id]
        table_open_bills = [bill for bill in open_bills if bill.table_id == table.id]
        table_paid_bills_total = sum(
            (bill.total_amount for bill in table_paid_bills),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))
        if not any([table_sessions, table_orders, table_paid_bills, table_open_bills]):
            continue
        table_rows.append(
            {
                "kind": "table",
                "id": str(table.id),
                "label": (
                    f"Стол {table.number} • сессий {len(table_sessions)} • "
                    f"заказов {len(table_orders)} • счетов {len(table_paid_bills)}"
                ),
                "subtitle": (
                    f"Выручка: {table_paid_bills_total} грн • "
                    f"открытых счетов: {len(table_open_bills)}"
                ),
            }
        )
    return _paginate_items(section="tables", items=table_rows, page=page)


def build_daily_table_detail(*, partner_id, table_id) -> DailyTableDetail:
    _partner, _day_start_local, day_start, day_end = _resolve_partner_day_bounds(
        partner_id=partner_id
    )
    table = Table.objects.get(partner_id=partner_id, id=table_id)
    sessions = list(
        TableSession.objects.filter(
            partner_id=partner_id,
            table_id=table_id,
            started_at__gte=day_start,
            started_at__lte=day_end,
        ).select_related("guest", "guest__telegram_account")
    )
    orders = list(
        Order.objects.filter(
            partner_id=partner_id,
            table_id=table_id,
            created_at__gte=day_start,
            created_at__lte=day_end,
        )
        .select_related(
            "guest",
            "guest__telegram_account",
            "assigned_employee",
            "assigned_employee__user",
        )
        .prefetch_related("items")
        .order_by("created_at")
    )
    paid_bills = list(
        Bill.objects.filter(
            partner_id=partner_id,
            table_id=table_id,
            status=Bill.Status.PAID,
            closed_at__gte=day_start,
            closed_at__lte=day_end,
        )
        .select_related("primary_guest", "primary_guest__telegram_account")
        .prefetch_related("payments", "items")
        .order_by("closed_at")
    )
    open_bills = list(
        Bill.objects.filter(
            partner_id=partner_id,
            table_id=table_id,
            status__in=[Bill.Status.DRAFT, Bill.Status.ISSUED, Bill.Status.PARTIALLY_PAID],
        )
        .select_related("primary_guest", "primary_guest__telegram_account")
        .prefetch_related("payments", "items")
        .order_by("created_at")
    )
    table_paid_bills_total = sum(
        (bill.total_amount for bill in paid_bills),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    open_bills_remaining_total = sum(
        (
            (bill.total_amount - bill.paid_amount).quantize(Decimal("0.01"))
            for bill in open_bills
        ),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))

    return DailyTableDetail(
        table_id=str(table.id),
        table_number=table.number,
        session_count=len(sessions),
        active_session_count=sum(
            1 for session in sessions if session.status == TableSession.Status.ACTIVE
        ),
        order_count=len(orders),
        paid_bill_count=len(paid_bills),
        open_bill_count=len(open_bills),
        paid_total=table_paid_bills_total,
        open_total=open_bills_remaining_total,
        sessions=[
            {
                "guest": _guest_label(session.guest),
                "started_at": _format_dt(session.started_at),
                "status": session.get_status_display(),
            }
            for session in sessions
        ],
        orders=[
            {
                "public_id": order.public_id,
                "guest": _guest_label(order.guest),
                "status": order.get_status_display(),
                "total_amount": order.total_amount,
                "paid": order.paid_at is not None,
                "received": order.received_at is not None,
                "positions": [
                    {
                        "name": item.item_name,
                        "quantity": item.quantity,
                    }
                    for item in order.items.all()
                ],
            }
            for order in orders
        ],
        paid_bills=[
            {
                "public_id": bill.public_id,
                "kind": bill.get_kind_display(),
                "guest": _guest_label(bill.primary_guest),
                "total_amount": bill.total_amount,
                "paid_at": _format_dt(bill.closed_at),
                "payment_method": _bill_payment_method_label(bill),
            }
            for bill in paid_bills
        ],
        open_bills=[
            {
                "public_id": bill.public_id,
                "kind": bill.get_kind_display(),
                "guest": _guest_label(bill.primary_guest),
                "remaining_amount": (bill.total_amount - bill.paid_amount).quantize(
                    Decimal("0.01")
                ),
                "status": bill.get_status_display(),
            }
            for bill in open_bills
        ],
    )


def build_daily_tails_page(*, partner_id, page: int = 1) -> ReportPage:
    open_bills = list(
        Bill.objects.filter(
            partner_id=partner_id,
            status__in=[Bill.Status.DRAFT, Bill.Status.ISSUED, Bill.Status.PARTIALLY_PAID],
        )
        .select_related("table", "primary_guest", "primary_guest__telegram_account")
        .prefetch_related("payments")
        .order_by("table__number", "-created_at")
    )
    unpaid_orders = list(
        Order.objects.filter(
            partner_id=partner_id,
            paid_at__isnull=True,
        )
        .exclude(status=Order.Status.CANCELED)
        .select_related("table", "guest", "guest__telegram_account")
        .order_by("table__number", "created_at")
    )
    paid_unreceived_orders = list(
        Order.objects.filter(
            partner_id=partner_id,
            paid_at__isnull=False,
            received_at__isnull=True,
        )
        .exclude(status=Order.Status.CANCELED)
        .select_related("table", "guest", "guest__telegram_account")
        .order_by("table__number", "created_at")
    )
    latest_open_requests: dict[tuple[str, str], BillingRequest] = {}
    for billing_request in (
        BillingRequest.objects.filter(
            partner_id=partner_id,
            status__in=[BillingRequest.Status.OPEN, BillingRequest.Status.AUTO_PREPARED],
        )
        .select_related("table", "guest", "guest__telegram_account")
        .order_by("-created_at")
    ):
        key = (str(billing_request.table_session_id), billing_request.request_type)
        latest_open_requests.setdefault(key, billing_request)

    items: list[dict] = []
    for bill in open_bills:
        items.append(
            {
                "kind": "bill",
                "public_id": bill.public_id,
                "label": (
                    f"Счёт #{bill.public_id} • {get_bill_context_label(bill)} • "
                    f"остаток "
                    f"{(bill.total_amount - bill.paid_amount).quantize(Decimal('0.01'))} грн"
                ),
                "subtitle": (
                    f"Статус: {bill.get_status_display()} • "
                    f"гость: {_guest_label(bill.primary_guest)}"
                ),
            }
        )
    for order in unpaid_orders:
        items.append(
            {
                "kind": "order",
                "public_id": order.public_id,
                "label": (
                    f"Заказ #{order.public_id} • {_order_context_label(order)} • "
                    f"{order.get_status_display()} • {order.total_amount} грн"
                ),
                "subtitle": f"Не оплачен • гость: {_guest_label(order.guest)}",
            }
        )
    for order in paid_unreceived_orders:
        items.append(
            {
                "kind": "order",
                "public_id": order.public_id,
                "label": (
                    f"Заказ #{order.public_id} • {_order_context_label(order)} • "
                    f"{order.get_status_display()} • {order.total_amount} грн"
                ),
                "subtitle": f"Оплачен, но не подтверждён • гость: {_guest_label(order.guest)}",
            }
        )
    for billing_request in latest_open_requests.values():
        items.append(
            {
                "kind": "request",
                "id": str(billing_request.id),
                "label": (
                    f"Запрос счёта • стол {billing_request.table.number} • "
                    f"{billing_request.get_request_type_display()}"
                ),
                "subtitle": (
                    f"Гость: {_guest_label(billing_request.guest)} • "
                    f"статус: {billing_request.get_status_display()}"
                ),
            }
        )
    priority = {"bill": 0, "order": 1, "request": 2}
    items.sort(key=lambda item: (priority.get(item["kind"], 99), item["label"]))
    return _paginate_items(section="tails", items=items, page=page)


def _staff_label(row: dict) -> str:
    full_name = " ".join(
        part.strip()
        for part in [row.get("created_by__first_name", ""), row.get("created_by__last_name", "")]
        if part
    ).strip()
    return full_name or row.get("created_by__username") or "Unknown"


def _staff_user_label(user) -> str:
    if user is None:
        return "—"
    full_name = user.get_full_name().strip()
    return full_name or user.username


def _guest_label(guest) -> str:
    if guest is None:
        return "несколько гостей"
    telegram_account = getattr(guest, "telegram_account", None)
    if telegram_account and telegram_account.username:
        return f"@{telegram_account.username}"
    if telegram_account:
        return str(telegram_account.telegram_id)
    return getattr(guest, "customer_code", "—")


def _bill_payment_method_label(bill: Bill) -> str:
    methods = [
        payment.get_method_display()
        for payment in bill.payments.all()
        if payment.status == Payment.Status.PAID
    ]
    unique_methods = list(dict.fromkeys(methods))
    if not unique_methods:
        return "не указано"
    if len(unique_methods) == 1:
        return unique_methods[0]
    return " / ".join(unique_methods)


def _order_context_label(order: Order) -> str:
    if order.table_id is not None:
        return f"стол {order.table.number}"
    return "заказ без привязки к столу"


def _format_dt(value) -> str:
    if value is None:
        return "--:--"
    return timezone.localtime(value).strftime("%H:%M")
