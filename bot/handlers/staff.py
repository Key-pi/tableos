import re
from decimal import Decimal
from uuid import UUID

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, Message
from asgiref.sync import sync_to_async
from django.db.models import Count
from django.utils import timezone

from apps.analytics.services import (
    build_daily_operations_report,
    build_daily_paid_bills_page,
    build_daily_table_detail,
    build_daily_tables_page,
    build_daily_tails_page,
    build_daily_walk_in_sales_page,
)
from apps.billing.models import Bill, BillAdjustment, BillingRequest, BillItem, Payment
from apps.billing.remote_orders import (
    RemoteOrderError,
    awaits_payment_on_receipt,
    awaits_transfer_check,
    cancel_unpaid_remote_order,
    confirm_transfer,
    map_link,
    mark_order_shipped,
    record_payment_on_receipt,
    reject_transfer,
    shipping_cost_label,
    tracking_link,
)
from apps.billing.repositories import (
    BillingOperationsRepository,
    BillingRequestRepository,
    BillRepository,
)
from apps.billing.services import (
    BillingServiceError,
    annotate_bill_adjustment,
    cancel_order,
    get_bill_context_label,
    issue_bill,
    pay_bill_with_bonuses,
    record_payment,
    remove_bill_lines,
    user_can_adjust_bills,
    void_bill,
)
from apps.billing.table_bills import (
    cancel_billing_request,
    create_personal_bills_for_table,
    create_shared_bill_for_table,
    mark_billing_request_processed,
)
from apps.bonuses.models import WalkInSale
from apps.bonuses.services import (
    BonusServiceError,
    get_redeemable_bonus_amount,
    preview_walk_in_sale_by_customer_code,
    register_walk_in_sale_from_menu_items_by_customer_code,
)
from apps.employees.models import EmployeeProfile
from apps.employees.selectors import get_staff_employee_by_telegram
from apps.menu.models import MenuCategory
from apps.menu.repositories import MenuItemRepository
from apps.notifications.models import StaffNotification
from apps.notifications.repositories import StaffNotificationRepository
from apps.notifications.services import (
    mark_staff_notification_read,
    mark_staff_notifications_read,
)
from apps.orders.models import Order
from apps.orders.repositories import OrderRepository
from apps.orders.selectors import get_open_orders_for_partner
from apps.orders.services import (
    OrderFlowError,
    get_available_staff_actions,
    is_order_paid,
    is_order_received,
    requested_time_label,
    transition_order_status,
)
from apps.tables.models import Table, TableSession, TableVisit
from apps.tables.services import (
    TableSessionError,
    can_close_table_session,
    close_table_session_if_settled,
    close_table_visit,
)
from bot.keyboards.staff import (
    DELIVERY_STATUS_LABELS,
    SHIPPING_STATUS_LABELS,
    STATUS_LABELS,
    build_staff_adjustment_done_keyboard,
    build_staff_adjustment_reason_keyboard,
    build_staff_bill_actions_keyboard,
    build_staff_bill_edit_keyboard,
    build_staff_billing_overview_keyboard,
    build_staff_billing_request_actions_keyboard,
    build_staff_home_keyboard,
    build_staff_notification_item_keyboard,
    build_staff_notifications_keyboard,
    build_staff_order_actions_keyboard,
    build_staff_orders_overview_keyboard,
    build_staff_receipt_refusal_confirm_keyboard,
    build_staff_report_section_keyboard,
    build_staff_report_summary_keyboard,
    build_staff_report_table_detail_keyboard,
    build_staff_report_walkin_detail_keyboard,
    build_staff_sale_code_prompt_keyboard,
    build_staff_sale_keyboard,
    build_staff_table_actions_keyboard,
    build_staff_tables_overview_keyboard,
    build_staff_transfer_reject_confirm_keyboard,
)
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.states.staff_sale import StaffBillStates, StaffQuickSaleStates
from bot.utils.html import escape_html
from core.i18n import N_, _, variants

router = Router()


async def _safe_edit_message_text(
    message: Message,
    text: str,
    *,
    reply_markup=None,
) -> bool:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return False
        raise
    return True


def _render_open_orders_summary(orders) -> str:
    if not orders:
        return _("Открытых заказов сейчас нет.")

    total_amount = sum(order.total_amount for order in orders)
    new_orders_count = sum(1 for order in orders if order.status == Order.Status.NEW)
    in_progress_count = len(orders) - new_orders_count
    unpaid_count = sum(1 for order in orders if not is_order_paid(order))
    unreceived_count = sum(1 for order in orders if not is_order_received(order))
    lines = [
        _("<b>Открытые заказы</b>"),
        _("Всего заказов: {count}", count=len(orders)),
        _("Сумма в работе: {total_amount} грн", total_amount=total_amount),
        _("Новых: {new_orders_count}", new_orders_count=new_orders_count),
        _("В работе: {in_progress_count}", in_progress_count=in_progress_count),
        _("Не оплачено: {unpaid_count}", unpaid_count=unpaid_count),
        _("Не получено: {unreceived_count}", unreceived_count=unreceived_count),
        _("Выберите заказ кнопками ниже."),
    ]
    return "\n".join(lines)


def _employee_label(employee: EmployeeProfile | None) -> str:
    if employee is None:
        return ""
    full_name = employee.user.get_full_name().strip()
    return full_name or employee.user.username


def _order_context_label(order: Order) -> str:
    if order.channel == Order.Channel.PICKUP:
        phone = order.guest.phone or _("не указан")
        return _(
            "Самовывоз: {requested_time}\n"
            "Телефон: {phone}",
            requested_time=requested_time_label(order),
            phone=escape_html(phone),
        )
    if order.channel == Order.Channel.DELIVERY:
        phone = order.guest.phone or _("не указан")
        lines = [
            _("Доставка: {requested_time}", requested_time=requested_time_label(order)),
            _("Адрес: {delivery_address}", delivery_address=escape_html(order.delivery_address)),
        ]
        if order.delivery_latitude is not None:
            link = map_link(order.delivery_latitude, order.delivery_longitude)
            lines.append(_("<a href=\"{link}\">Открыть на карте</a>", link=escape_html(link)))
        lines.append(_("Телефон: {phone}", phone=escape_html(phone)))
        return "\n".join(lines)
    if order.channel == Order.Channel.SHIPPING:
        phone = order.guest.phone or _("не указан")
        lines = [
            _(
                "Отправка: {shipping_carrier}",
                shipping_carrier=escape_html(order.get_shipping_carrier_display()),
            ),
            _(
                "Получатель: {shipping_destination}",
                shipping_destination=escape_html(order.shipping_destination),
            ),
            _("Телефон: {phone}", phone=escape_html(phone)),
            _("Пересылка: {shipping_cost}", shipping_cost=escape_html(shipping_cost_label(order))),
        ]
        if order.tracking_number:
            link = tracking_link(order.shipping_carrier, order.tracking_number)
            number = escape_html(order.tracking_number)
            lines.append(
                _(
                    "ТТН: <a href=\"{link}\">{number}</a>",
                    link=escape_html(link),
                    number=number,
                ) if link else _(
                    "ТТН: {number}",
                    number=number,
                )
            )
        return "\n".join(lines)
    if order.table_id is not None:
        return _("Стол: #{number}", number=order.table.number)
    return _("Сценарий: заказ без привязки к столу")


def _order_actions_keyboard(order: Order):
    is_shipping = order.channel == Order.Channel.SHIPPING
    return build_staff_order_actions_keyboard(
        order_public_id=order.public_id,
        available_statuses=get_available_staff_actions(order.status, channel=order.channel),
        transfer_check_pending=awaits_transfer_check(order),
        receipt_payment_pending=awaits_payment_on_receipt(order),
        cash_on_delivery=is_shipping,
        ship_needs_tracking=is_shipping and order.status == Order.Status.READY,
        status_labels=_status_labels(order),
        can_refund=order.paid_at is not None and order.status != Order.Status.CANCELED,
    )


def _status_labels(order: Order) -> dict[str, str]:
    if order.channel == Order.Channel.DELIVERY:
        return DELIVERY_STATUS_LABELS
    if order.channel == Order.Channel.SHIPPING:
        return SHIPPING_STATUS_LABELS
    return STATUS_LABELS


def _order_payment_label(order: Order) -> str:
    if awaits_transfer_check(order):
        to_transfer = order.total_amount - order.bonus_spent
        bonus_note = _(
            " (ещё {bonus_spent} бонусами)",
            bonus_spent=order.bonus_spent,
        ) if order.bonus_spent else ""
        return _(
            "ждёт подтверждения перевода {to_transfer} грн{bonus_note}",
            to_transfer=to_transfer,
            bonus_note=bonus_note,
        )
    if awaits_payment_on_receipt(order):
        to_pay = order.total_amount - order.bonus_spent
        bonus_note = _(
            " (ещё {bonus_spent} бонусами)",
            bonus_spent=order.bonus_spent,
        ) if order.bonus_spent else ""
        return _("при получении, {to_pay} грн{bonus_note}", to_pay=to_pay, bonus_note=bonus_note)
    return _("Оплачен") if is_order_paid(order) else _("Не оплачен")


def _order_short_context_label(order: Order) -> str:
    if order.channel == Order.Channel.PICKUP:
        return _("Самовывоз")
    if order.channel == Order.Channel.DELIVERY:
        return _("Доставка")
    if order.channel == Order.Channel.SHIPPING:
        return _("Почта")
    if order.table_id is not None:
        return f"T{order.table.number}"
    return _("Вне стола")


def _render_order_card(order: Order) -> str:
    items_text = ", ".join(
        f"{escape_html(item.item_name)} x{item.quantity}" for item in order.items.all()
    )
    guest_label = order.guest.telegram_account.username or order.guest.telegram_account.telegram_id
    comment_text = order.comment or _("Без комментария")
    assignee_label = _employee_label(order.assigned_employee) or _("Пока не назначен")
    payment_label = _order_payment_label(order)
    received_label = _("Получен") if is_order_received(order) else _("Не получен")
    return (
        _(
            "<b>Заказ #{public_id}</b>\n"
            "{order_context}\n"
            "Гость: {guest}\n"
            "Ответственный: {assignee}\n"
            "Статус: {status}\n"
            "Оплата: {payment}\n"
            "Получение: {received}\n"
            "Сумма: {total_amount} грн\n"
            "Позиции: {items_text}\n"
            "Комментарий: {comment_text}",
            public_id=order.public_id,
            order_context=_order_context_label(order),
            guest=escape_html(guest_label),
            assignee=escape_html(assignee_label),
            status=order.get_status_display(),
            payment=payment_label,
            received=received_label,
            total_amount=order.total_amount,
            items_text=items_text or '-',
            comment_text=escape_html(comment_text),
        )
    )


def _build_order_shortcuts(orders: list[Order]) -> list[tuple[str, str, bool]]:
    short_status_labels = {
        Order.Status.NEW: _("Новый"),
        Order.Status.ACCEPTED: _("В работе"),
        Order.Status.PREPARING: _("Готовим"),
        Order.Status.READY: _("Готов"),
        Order.Status.DELIVERING: _("Несут"),
        Order.Status.COMPLETED: _("Получен"),
    }
    shortcuts: list[tuple[str, str, bool]] = []
    for order in orders[:8]:
        payment_badge = _("не опл.") if not is_order_paid(order) else _("опл.")
        label = (
            f"#{order.public_id} • {_order_short_context_label(order)} • "
            f"{short_status_labels.get(order.status, order.get_status_display())} • {payment_badge}"
        )
        shortcuts.append(
            (
                order.public_id,
                label,
                order.status == Order.Status.NEW,
            )
        )
    return shortcuts


def _render_staff_notifications(notifications) -> str:
    if not notifications:
        return _("Новых внутренних уведомлений нет.")

    return (
        _(
            "<b>Уведомления персоналу</b>\n"
            "Новых уведомлений: {count}\n"
            "Карточки уведомлений отправлены ниже.",
            count=len(notifications),
        )
    )


def _render_staff_notification_card(notification) -> str:
    order_label = f"#{notification.order.public_id}" if notification.order_id else "-"
    return (
        _(
            "<b>{title}</b>\n"
            "{message}\n"
            "Заказ: {order}",
            title=escape_html(notification.title),
            message=escape_html(notification.message),
            order=order_label,
        )
    )


def _message_text(message: Message | None) -> str:
    if message is None:
        return ""
    return (getattr(message, "html_text", None) or getattr(message, "text", "") or "").strip()


def _is_staff_overview_message(message: Message | None, title: str) -> bool:
    text = _message_text(message)
    return text.startswith(f"<b>{title}</b>") or text.startswith(title)


# A staff reply to this prompt is the parcel's ТТН (shipping, PQ-034).
TRACKING_PROMPT = N_("📦 ТТН для заказа")


_STAFF_OPERATIONS_ROLES = frozenset(
    {
        "owner",
        "manager",
        "cashier",
    }
)


def _module_enabled(employee: EmployeeProfile, check) -> bool:
    # The resolver attaches the partner's BotContent; if it is missing (e.g. a
    # helper called outside the staff flow) fall back to module-enabled so role
    # checks stay backward compatible.
    content = getattr(employee, "bot_content", None)
    if content is None:
        return True
    return check(content)


def _can_use_quick_sale(employee: EmployeeProfile) -> bool:
    return employee.user.role in _STAFF_OPERATIONS_ROLES and _module_enabled(
        employee, lambda content: content.supports_quick_sale()
    )


def _ensure_quick_sale_allowed(employee: EmployeeProfile) -> None:
    if employee.user.role not in _STAFF_OPERATIONS_ROLES:
        raise OrderFlowError(
            _("Быстрая продажа сейчас доступна только владельцу, менеджеру или кассиру.")
        )
    if not _module_enabled(employee, lambda content: content.supports_quick_sale()):
        raise OrderFlowError(_("Модуль быстрых продаж отключён для этого заведения."))


def _can_view_day_report(employee: EmployeeProfile) -> bool:
    return employee.user.role in _STAFF_OPERATIONS_ROLES and _module_enabled(
        employee, lambda content: content.supports_reports()
    )


def _can_manage_billing(employee: EmployeeProfile) -> bool:
    return employee.user.role in _STAFF_OPERATIONS_ROLES and _module_enabled(
        employee, lambda content: content.supports_billing()
    )


def _can_view_open_orders(employee: EmployeeProfile) -> bool:
    # Open orders feed is available to any staff role; it only depends on whether
    # the partner runs the orders module (table, delivery, or pickup).
    return _module_enabled(employee, lambda content: content.supports_orders())


def _ensure_open_orders_allowed(employee: EmployeeProfile) -> None:
    if not _can_view_open_orders(employee):
        raise OrderFlowError(_("Модуль заказов отключён для этого заведения."))


def _can_view_tables(employee: EmployeeProfile) -> bool:
    # "Столы" is gated by the tables module (plus an operations role). Per-table
    # bill creation inside the feed still requires the billing module separately
    # via _can_manage_billing.
    return employee.user.role in _STAFF_OPERATIONS_ROLES and _module_enabled(
        employee, lambda content: content.supports_tables()
    )


def _build_staff_home_markup(employee: EmployeeProfile | None):
    if employee is None:
        return None
    return build_staff_home_keyboard(
        can_quick_sale=_can_use_quick_sale(employee),
        can_view_day_report=_can_view_day_report(employee),
        can_manage_billing=_can_manage_billing(employee),
        can_view_orders=_can_view_open_orders(employee),
        can_view_tables=_can_view_tables(employee),
    )


def _render_day_report(report) -> str:
    lines = [
        _("<b>Отчёт дня</b>"),
        _("Заведение: {partner_name}", partner_name=escape_html(report.partner_name)),
        _("Дата: {local_date}", local_date=report.local_date_label),
        "",
        _("<b>Деньги</b>"),
        _("Всего выручка: {total_revenue} грн", total_revenue=report.total_revenue),
        _("По счетам: {bill_revenue} грн", bill_revenue=report.bill_revenue),
        _("Быстрые продажи: {walk_in_revenue} грн", walk_in_revenue=report.walk_in_revenue),
        *([_(
            "Возвраты: −{refunds_total} грн",
            refunds_total=report.refunds_total,
        )] if report.refunds_total else []),
        "",
        _("<b>Операции</b>"),
        _("Оплаченных счетов: {paid_bills_count}", paid_bills_count=report.paid_bills_count),
        _("Быстрых продаж: {walk_in_sales_count}", walk_in_sales_count=report.walk_in_sales_count),
        _(
            "Создано заказов: {created_orders_count}",
            created_orders_count=report.created_orders_count,
        ),
        _("Открытых заказов: {open_orders_count}", open_orders_count=report.open_orders_count),
        _("Открытых счетов: {open_bills_count}", open_bills_count=report.open_bills_count),
    ]
    if report.channel_breakdown:
        lines.extend(["", _("<b>По способам заказа</b>")])
        for item in report.channel_breakdown:
            lines.append(
                _(
                    "• {label}: заказов {orders_count}, оплачено {revenue} грн",
                    label=escape_html(item['label']),
                    orders_count=item['orders_count'],
                    revenue=item['revenue'],
                )
            )
    lines.extend(["", _("<b>Методы оплаты</b>")])
    for method, amount in report.payment_method_totals.items():
        count = report.payment_method_counts.get(method, 0)
        if amount <= 0 and count == 0:
            continue
        lines.append(_(
            "• {method}: {amount} грн ({count})",
            method=method,
            amount=amount,
            count=count,
        ))

    if report.staff_breakdown:
        lines.extend(["", _("<b>По сотрудникам</b>")])
        for item in report.staff_breakdown[:8]:
            lines.append(
                _(
                    "• {label}: {total_amount} грн (платежей {payments_count}, быстрых продаж "
                    "{walk_in_sales_count})",
                    label=escape_html(item['label']),
                    total_amount=item['total_amount'],
                    payments_count=item['payments_count'],
                    walk_in_sales_count=item['walk_in_sales_count'],
                )
            )
    return "\n".join(lines)


def _render_report_section(page_result) -> str:
    titles = {
        "bills": _("Счета дня"),
        "walkins": _("Быстрые продажи"),
        "tables": _("Столы за день"),
        "tails": _("Хвосты"),
    }
    lines = [
        f"<b>{titles.get(page_result.section, _('Журнал дня'))}</b>",
        _("Записей: {total_count}", total_count=page_result.total_count),
        _(
            "Страница: {page}/{total_pages}",
            page=page_result.page,
            total_pages=page_result.total_pages,
        ),
    ]
    if not page_result.items:
        lines.extend(["", _("Записей для этого раздела сейчас нет.")])
        return "\n".join(lines)

    for item in page_result.items:
        lines.extend(
            [
                "",
                escape_html(item["label"]),
                escape_html(item["subtitle"]),
            ]
        )
    return "\n".join(lines)


def _build_report_section_shortcuts(page_result) -> list[tuple[str, str]]:
    shortcuts: list[tuple[str, str]] = []
    for item in page_result.items:
        if item["kind"] == "bill":
            target = item.get("public_id")
            if target:
                shortcuts.append((_(
                    "Открыть {label}",
                    label=item['label'],
                ), f"staffbillopen:{target}"))
        elif item["kind"] == "order":
            target = item.get("public_id")
            if target:
                shortcuts.append((_(
                    "Открыть {label}",
                    label=item['label'],
                ), f"stafforderopen:{target}"))
        elif item["kind"] == "table":
            target = item.get("id")
            if target:
                shortcuts.append(
                    (
                        _("Открыть {label}", label=item['label']),
                        f"staffreporttableopen:{target}:{page_result.page}",
                    )
                )
        elif item["kind"] == "request":
            target = item.get("id")
            if target:
                shortcuts.append((_(
                    "Открыть {label}",
                    label=item['label'],
                ), f"staffbillreqrefresh:{target}"))
        elif item["kind"] == "walkin":
            target = item.get("id")
            if target:
                shortcuts.append(
                    (
                        _("Открыть {label}", label=item['label']),
                        f"staffreportsaleopen:{target}:{page_result.page}",
                    )
                )
    return shortcuts


def _render_walk_in_sale_card(sale: WalkInSale) -> str:
    sale_items = list(sale.items.all())
    staff_label = (
        sale.created_by.get_full_name().strip() or sale.created_by.username
        if sale.created_by
        else "—"
    )
    lines = [
        _("<b>Быстрая продажа</b>"),
        _("Время: {created_at}", created_at=timezone.localtime(sale.created_at).strftime('%H:%M')),
        _("Код клиента: {loyalty}", loyalty=sale.loyalty_label),
        _("Сумма: {amount} грн", amount=sale.amount),
        _(
            "Бонусов начислено: {bonus_awarded_amount}",
            bonus_awarded_amount=sale.bonus_awarded_amount,
        ),
        _("Сотрудник: {staff}", staff=escape_html(staff_label)),
        _("Комментарий: {comment}", comment=escape_html(sale.comment) or '—'),
    ]
    if sale_items:
        lines.extend(["", _("Позиции:")])
        for item in sale_items:
            lines.append(
                _(
                    "• {item_name} x{quantity} • {unit_price} грн",
                    item_name=escape_html(item.item_name),
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                )
            )
    return "\n".join(lines)


def _render_report_table_detail(detail) -> str:
    lines = [
        _("<b>Стол #{table_number} за день</b>", table_number=detail.table_number),
        _("Сессий: {session_count}", session_count=detail.session_count),
        _(
            "Активных сессий: {active_session_count}",
            active_session_count=detail.active_session_count,
        ),
        _("Заказов: {order_count}", order_count=detail.order_count),
        _("Оплаченных счетов: {paid_bill_count}", paid_bill_count=detail.paid_bill_count),
        _("Открытых счетов: {open_bill_count}", open_bill_count=detail.open_bill_count),
        _("Оплачено: {paid_total} грн", paid_total=detail.paid_total),
        _("В остатке по счетам: {open_total} грн", open_total=detail.open_total),
    ]

    if detail.sessions:
        lines.extend(["", _("Сессии:")])
        for session in detail.sessions[:6]:
            lines.append(
                f"• {session['started_at']} • {escape_html(session['guest'])} • {session['status']}"
            )

    if detail.orders:
        lines.extend(["", _("Заказы:")])
        for order in detail.orders[:8]:
            payment_label = _("опл.") if order["paid"] else _("не опл.")
            receive_label = _("получен") if order["received"] else _("не получен")
            lines.append(
                _(
                    "• #{public_id} • {guest} • {status} • {total_amount} грн • {payment} • "
                    "{receive}",
                    public_id=order['public_id'],
                    guest=escape_html(order['guest']),
                    status=order['status'],
                    total_amount=order['total_amount'],
                    payment=payment_label,
                    receive=receive_label,
                )
            )
            positions = ", ".join(
                f"{escape_html(item['name'])} x{item['quantity']}"
                for item in order["positions"][:4]
            )
            if positions:
                lines.append(_("  Позиции: {positions}", positions=positions))

    if detail.paid_bills:
        lines.extend(["", _("Оплаченные счета:")])
        for bill in detail.paid_bills[:6]:
            lines.append(
                _(
                    "• #{public_id} • {kind} • {guest} • {total_amount} грн • {payment_method} • "
                    "{paid_at}",
                    public_id=bill['public_id'],
                    kind=bill['kind'],
                    guest=escape_html(bill['guest']),
                    total_amount=bill['total_amount'],
                    payment_method=bill['payment_method'],
                    paid_at=bill['paid_at'],
                )
            )

    if detail.open_bills:
        lines.extend(["", _("Открытые счета:")])
        for bill in detail.open_bills[:6]:
            lines.append(
                _(
                    "• #{public_id} • {kind} • {guest} • остаток {remaining_amount} грн • "
                    "{status}",
                    public_id=bill['public_id'],
                    kind=bill['kind'],
                    guest=escape_html(bill['guest']),
                    remaining_amount=bill['remaining_amount'],
                    status=bill['status'],
                )
            )

    return "\n".join(lines)


def _guest_label(guest) -> str:
    telegram_account = getattr(guest, "telegram_account", None)
    if telegram_account and telegram_account.username:
        return f"@{telegram_account.username}"
    if telegram_account:
        return str(telegram_account.telegram_id)
    return guest.customer_code


def _render_billing_overview(requests, bills, unbilled_orders) -> str:
    off_table_unbilled_count = sum(1 for order in unbilled_orders if order.table_id is None)
    lines = [
        _("<b>Счета и оплаты</b>"),
        _("Открытых запросов счёта: {count}", count=len(requests)),
        _("Открытых счетов: {count}", count=len(bills)),
        _("Неоплаченных заказов без счёта: {count}", count=len(unbilled_orders)),
    ]
    if off_table_unbilled_count:
        lines.append(_(
            "Из них без привязки к столу: {off_table_unbilled_count}",
            off_table_unbilled_count=off_table_unbilled_count,
        ))
    lines.append(_("Ниже заказы без счёта и карточки для работы."))
    return "\n".join(lines)


def _build_active_table_shortcuts(
    active_sessions,
    unbilled_orders,
    visit_counts: dict[str, tuple[int, int]] | None = None,
) -> list[tuple[str, str]]:
    """Table shortcuts; ``visit_counts`` maps table id -> (number, active visits)."""

    tables_map: dict[str, dict] = {}
    for session in active_sessions:
        table_id = str(session.table_id)
        data = tables_map.setdefault(
            table_id,
            {
                "table_number": session.table.number,
                "guest_ids": set(),
                "unbilled_orders": 0,
            },
        )
        data["guest_ids"].add(session.guest_id)
    for order in unbilled_orders:
        if order.table_id is None:
            continue
        table_id = str(order.table_id)
        data = tables_map.setdefault(
            table_id,
            {
                "table_number": order.table.number,
                "guest_ids": set(),
                "unbilled_orders": 0,
            },
        )
        data["unbilled_orders"] += 1
    for table_id, (table_number, _count) in (visit_counts or {}).items():
        tables_map.setdefault(
            table_id,
            {"table_number": table_number, "guest_ids": set(), "unbilled_orders": 0},
        )
    return [
        (
            table_id,
            (
                # ⚠: several open visits at one table need staff attention (ADR-0007).
                _(
                    "{value}Стол {table_number} • гостей {count} • без счёта {unbilled_orders}",
                    value='⚠ ' if (visit_counts or {}).get(table_id, (0, 0))[1] > 1 else '',
                    table_number=data['table_number'],
                    count=len(data['guest_ids']),
                    unbilled_orders=data['unbilled_orders'],
                )
            ),
        )
        for table_id, data in sorted(tables_map.items(), key=lambda item: item[1]["table_number"])
    ][:8]


def _build_unbilled_order_shortcuts(orders: list[Order]) -> list[tuple[str, str, str | None]]:
    shortcuts: list[tuple[str, str, str | None]] = []
    prioritized_orders = [
        order
        for order in orders
        if order.status in {Order.Status.READY, Order.Status.DELIVERING, Order.Status.COMPLETED}
    ]
    source_orders = prioritized_orders or orders
    for order in source_orders[:8]:
        short_status_label = (
            _("Получен")
            if order.status == Order.Status.COMPLETED
            else order.get_status_display()
        )
        label = (
            _(
                "К оплате #{public_id} • {order_short_context} • {short_status} • {total_amount} "
                "грн",
                public_id=order.public_id,
                order_short_context=_order_short_context_label(order),
                short_status=short_status_label,
                total_amount=order.total_amount,
            )
        )
        shortcuts.append(
            (
                order.public_id,
                label,
                str(order.table_id) if order.table_id is not None else None,
            )
        )
    return shortcuts


def _render_table_card(
    *,
    table: Table,
    active_sessions,
    open_orders,
    unbilled_orders,
    open_bills,
    visits=None,
) -> str:
    lines = [
        _("<b>Стол #{number}</b>", number=table.number),
        _("Активных гостей: {count}", count=len(active_sessions)),
        _("Открытых заказов: {count}", count=len(open_orders)),
        _("Заказов без счёта: {count}", count=len(unbilled_orders)),
        _("Открытых счетов: {count}", count=len(open_bills)),
    ]
    if active_sessions:
        guest_lines = []
        for session in active_sessions[:4]:
            guest_lines.append(
                f"• {escape_html(_guest_label(session.guest))} c "
                f"{session.started_at.strftime('%H:%M')}"
            )
        lines.extend(["", _("Гости:")] + guest_lines)
    if visits:
        lines.append("")
        if len(visits) > 1:
            lines.append(_("⚠ За столом несколько визитов — рассчитайте и закройте предыдущий."))
        lines.append(_("Визиты:"))
        for summary in visits:
            lines.append(
                _(
                    "• {title}: гостей {guests}, без счёта {unbilled}, открытых счетов "
                    "{open_bills}",
                    title=summary['title'],
                    guests=summary['guests'],
                    unbilled=summary['unbilled'],
                    open_bills=summary['open_bills'],
                )
            )
    return "\n".join(lines)


def _render_tables_overview(active_sessions, unbilled_orders) -> str:
    table_shortcuts = _build_active_table_shortcuts(active_sessions, unbilled_orders)
    return (
        _(
            "<b>Активные столы</b>\n"
            "Столов в работе: {count}\n"
            "Активных гостей: {count2}\n"
            "Выберите стол кнопками ниже.",
            count=len(table_shortcuts),
            count2=len(active_sessions),
        )
    )


def _build_session_close_actions(active_sessions) -> list[tuple[str, str]]:
    actions: list[tuple[str, str]] = []
    for session in active_sessions[:6]:
        can_close, _reason = can_close_table_session(session)
        label = _("Закрыть {guest}", guest=_guest_label(session.guest))
        if not can_close:
            label = _("Проверить {guest}", guest=_guest_label(session.guest))
        actions.append((str(session.id), label))
    return actions


def _render_billing_request_card(billing_request: BillingRequest) -> str:
    request_type_labels = {
        BillingRequest.RequestType.PERSONAL: _("Счёт на гостя"),
        BillingRequest.RequestType.SHARED: _("Счёт на весь стол"),
        BillingRequest.RequestType.CUSTOM_SPLIT: _("Нестандартное разделение"),
    }
    request_type_label = request_type_labels.get(
        billing_request.request_type,
        billing_request.request_type,
    )
    linked_bill = (
        f"#{billing_request.bill.public_id}"
        if billing_request.bill_id and billing_request.bill
        else _("ещё не подготовлен")
    )
    return (
        _(
            "<b>Запрос счёта</b>\n"
            "Стол: #{number}\n"
            "Гость: {guest}\n"
            "Тип: {request_type}\n"
            "Статус: {status}\n"
            "Счёт: {linked_bill}\n"
            "Комментарий: {note}",
            number=billing_request.table.number,
            guest=escape_html(_guest_label(billing_request.guest)),
            request_type=request_type_label,
            status=billing_request.get_status_display(),
            linked_bill=linked_bill,
            note=escape_html(billing_request.note) or _('без комментария'),
        )
    )


def _render_bill_card(bill: Bill) -> str:
    _unused, bill_text = _build_bill_card_view(bill)
    return bill_text


def _build_bill_card_view(bill: Bill) -> tuple[bool, str]:
    remaining_amount = (bill.total_amount - bill.paid_amount).quantize(Decimal("0.01"))
    guest_label = _guest_label(bill.primary_guest) if bill.primary_guest_id else _(
        "несколько гостей",
    )
    bonus_hint = ""
    can_pay_with_bonuses = False
    active_items = [item for item in bill.items.all() if item.is_active]
    removed_count = sum(1 for item in bill.items.all() if not item.is_active)
    item_lines = [
        _(
            "• {item_name} x{quantity} = {line_total} грн",
            item_name=escape_html(item.item_name),
            quantity=item.quantity,
            line_total=item.line_total,
        )
        for item in active_items[:8]
    ]
    if removed_count:
        item_lines.append(_(
            "Убрано позиций: {removed_count} (см. историю счёта)",
            removed_count=removed_count,
        ))
    if bill.primary_guest_id:
        redeemable_amount = (
            get_redeemable_bonus_amount(
                partner_id=bill.partner_id,
                guest=bill.primary_guest,
                purchase_total=bill.total_amount,
            )
            if bill.total_amount > 0
            else Decimal("0.00")
        )
        can_pay_with_bonuses = redeemable_amount > 0
        bonus_hint = (
            _(
                "\n"
                "Оплачено бонусами: {bonus_spent_amount} грн",
                bonus_spent_amount=bill.bonus_spent_amount,
            )
            + (
                _(
                    "\n"
                    "Можно оплатить бонусами: {redeemable_amount} грн",
                    redeemable_amount=redeemable_amount,
                )
                if bill.bonus_spent_amount == 0
                else ""
            )
            + _(
                "\n"
                "Баланс гостя: {loyalty_balance} бонусов (1 бонус = 1 грн)",
                loyalty_balance=bill.primary_guest.loyalty_balance,
            )
        )
    if bill.bonus_spent_amount > 0 or bill.paid_amount > 0:
        can_pay_with_bonuses = False
    if bill.status in {Bill.Status.CANCELED, Bill.Status.PAID}:
        can_pay_with_bonuses = False

    bill_text = (
        _(
            "<b>Счёт #{public_id}</b>\n"
            "Контекст: {bill_context}\n"
            "Гость: {guest}\n"
            "Тип: {kind}\n"
            "Статус: {status}\n"
            "Заказов в счёте: {count}\n"
            "Сумма: {total_amount} грн\n"
            "Оплачено: {paid_amount} грн\n"
            "Остаток: {remaining_amount} грн\n"
            "Комментарий: {label}\n"
            "Позиции:\n"
            "{item_lines}{bonus_hint}",
            public_id=bill.public_id,
            bill_context=escape_html(get_bill_context_label(bill)),
            guest=escape_html(guest_label),
            kind=bill.get_kind_display(),
            status=bill.get_status_display(),
            count=bill.bill_orders.count(),
            total_amount=bill.total_amount,
            paid_amount=bill.paid_amount,
            remaining_amount=remaining_amount,
            label=escape_html(bill.label) or _('без комментария'),
            item_lines=chr(10).join(item_lines) if item_lines else '—',
            bonus_hint=bonus_hint,
        )
    )
    return can_pay_with_bonuses, bill_text


_OPEN_BILL_STATUSES = frozenset(
    {Bill.Status.DRAFT, Bill.Status.ISSUED, Bill.Status.PARTIALLY_PAID}
)


def _bill_change_flags(bill: Bill, actor_user) -> tuple[bool, bool]:
    """Return (can_edit, can_void) for the bill card buttons (PQ-028)."""

    if bill.status not in _OPEN_BILL_STATUSES:
        return False, False
    if not user_can_adjust_bills(actor_user, bill.partner_id):
        return False, False
    has_active_lines = any(item.is_active for item in bill.items.all())
    can_edit = has_active_lines and bill.bonus_spent_amount == 0
    can_void = bill.paid_amount == 0
    return can_edit, can_void


def _build_bill_card_message(bill: Bill, actor_user):
    """Return (text, keyboard) for a staff bill card."""

    remaining_amount = (bill.total_amount - bill.paid_amount).quantize(Decimal("0.01"))
    can_pay_with_bonuses, bill_text = _build_bill_card_view(bill)
    can_edit, can_void = _bill_change_flags(bill, actor_user)
    markup = build_staff_bill_actions_keyboard(
        bill_public_id=bill.public_id,
        can_issue=bill.status == Bill.Status.DRAFT,
        can_take_payment=remaining_amount > 0 and bill.status in _OPEN_BILL_STATUSES,
        can_pay_with_bonuses=can_pay_with_bonuses,
        can_edit=can_edit,
        can_void=can_void,
    )
    return bill_text, markup


def _build_bill_edit_view(bill: Bill, actor_user):
    if not user_can_adjust_bills(actor_user, bill.partner_id):
        raise BillingServiceError(_("Нет права изменять счета. Его выдаёт владелец заведения."))
    if bill.status not in _OPEN_BILL_STATUSES:
        raise BillingServiceError(_("Этот счёт уже закрыт, изменить его нельзя."))
    if bill.bonus_spent_amount > 0:
        raise BillingServiceError(
            _("По счёту применены бонусы: его можно только аннулировать целиком.")
        )
    active_items = [
        item for item in bill.items.select_related("order").all() if item.is_active
    ]
    lines = [
        _("<b>Изменение счёта #{public_id}</b>", public_id=bill.public_id),
        _(
            "Сумма: {total_amount} грн, оплачено: {paid_amount} грн",
            total_amount=bill.total_amount,
            paid_amount=bill.paid_amount,
        ),
        "",
        _("Уберите позицию (останется в истории счёта) или отмените заказ целиком."),
    ]
    line_actions = [
        (
            str(item.order_item_id),
            _(
                "Убрать: {item_name} x{quantity} ({line_total} грн)",
                item_name=item.item_name,
                quantity=item.quantity,
                line_total=item.line_total,
            ),
        )
        for item in active_items[:8]
    ]
    order_public_ids = list(dict.fromkeys(item.order.public_id for item in active_items))
    order_actions = [
        (public_id, _(
            "Отменить заказ #{public_id}",
            public_id=public_id,
        )) for public_id in order_public_ids[:4]
    ]
    markup = build_staff_bill_edit_keyboard(
        bill_public_id=bill.public_id,
        line_actions=line_actions,
        order_actions=order_actions,
    )
    return "\n".join(lines), markup


def _build_quick_sale_items_payload(items_map: dict[str, int]) -> list[dict]:
    return [
        {"menu_item_id": item_id, "quantity": quantity}
        for item_id, quantity in items_map.items()
        if quantity > 0
    ]


def _require_staff_sale_preview_state(data: dict) -> dict:
    preview_chat_id = data.get("preview_chat_id")
    preview_message_id = data.get("preview_message_id")
    customer_code = data.get("customer_code")
    if preview_chat_id is None or preview_message_id is None or customer_code is None:
        raise OrderFlowError(_("Черновик быстрой продажи устарел. Начните заново."))
    return {
        "preview_chat_id": preview_chat_id,
        "preview_message_id": preview_message_id,
        "customer_code": customer_code,
        "items": data.get("items", {}),
        "selected_category_id": data.get("selected_category_id"),
        "comment": data.get("comment", ""),
        "redeem_bonus": data.get("redeem_bonus", False),
    }


def _compose_staff_sale_view(
    *,
    partner_id,
    customer_code: str,
    items_map: dict[str, int],
    selected_category_id: str | None,
    comment: str = "",
    redeem_bonus: bool = False,
):
    categories = list(
        MenuCategory.objects.filter(partner_id=partner_id, is_active=True).order_by(
            "sort_order",
            "name",
        )
    )
    if not categories:
        raise OrderFlowError(_("Для быстрой продажи сначала добавьте активные категории меню."))

    category_ids = {str(category.id) for category in categories}
    if selected_category_id not in category_ids:
        selected_category_id = str(categories[0].id)

    guest_preview = None
    draft_payload = _build_quick_sale_items_payload(items_map)
    if draft_payload:
        guest_preview = preview_walk_in_sale_by_customer_code(
            partner_id=partner_id,
            customer_code=customer_code,
            redeem_bonus=redeem_bonus,
            items=draft_payload,
        )
    else:
        guest_preview = preview_walk_in_sale_by_customer_code(
            partner_id=partner_id,
            customer_code=customer_code,
            items=[],
        )

    category_items = list(
        MenuItemRepository.active_for_partner(partner_id).filter(category_id=selected_category_id)[:12]
    )
    can_redeem = guest_preview.guest is not None and guest_preview.redeemable_bonus_amount > 0
    redeem_active = redeem_bonus and can_redeem
    keyboard = build_staff_sale_keyboard(
        categories=[(str(category.id), category.name) for category in categories],
        selected_category_id=selected_category_id,
        category_items=[
            (str(item.id), item.name, f"{Decimal(item.price):.2f}")
            for item in category_items
        ],
        draft_items=[
            (str(item["menu_item_id"]), item["item_name"], item["quantity"])
            for item in guest_preview.items
        ],
        can_confirm=bool(guest_preview.items),
        has_comment=bool(comment),
        can_redeem=can_redeem,
        redeem_active=redeem_active,
    )

    if guest_preview.guest is None:
        lines = [
            _("<b>Быстрая продажа</b>"),
            _("Покупатель: Аноним (без кода клиента)"),
            _("Бонусы за эту продажу не начисляются."),
            "",
        ]
    else:
        telegram_account = guest_preview.guest.telegram_account
        guest_identity = (
            f"@{telegram_account.username}"
            if telegram_account.username
            else str(telegram_account.telegram_id)
        )
        first_visit = (
            guest_preview.guest.first_visit_at.strftime("%d.%m.%Y")
            if guest_preview.guest.first_visit_at
            else _("пока нет")
        )
        last_visit = (
            guest_preview.guest.last_visit_at.strftime("%d.%m.%Y %H:%M")
            if guest_preview.guest.last_visit_at
            else _("ещё не заходил за стол")
        )

        lines = [
            _("<b>Быстрая продажа</b>"),
            _("Код клиента: {customer_code}", customer_code=guest_preview.guest.customer_code),
            f"Telegram: {escape_html(guest_identity)}",
            _(
                "Текущий баланс: {loyalty_balance} бонусов",
                loyalty_balance=guest_preview.guest.loyalty_balance,
            ),
            _("Первый визит: {first_visit}", first_visit=first_visit),
            _("Последний визит: {last_visit}", last_visit=last_visit),
            "",
        ]
    if comment:
        lines.extend(
            [
                _("Комментарий: {comment}", comment=escape_html(comment)),
                "",
            ]
        )
    if guest_preview.items:
        lines.append(_("Позиции:"))
        for item in guest_preview.items:
            line_total = item["unit_price"] * item["quantity"]
            lines.append(
                _(
                    "• {item_name} x{quantity} = {line_total} грн",
                    item_name=escape_html(item['item_name']),
                    quantity=item['quantity'],
                    line_total=line_total,
                )
            )
        lines.extend(
            [
                "",
                _("Итого: {total_amount} грн", total_amount=guest_preview.total_amount),
                _(
                    "Ожидаемое начисление: +{projected_bonus_amount} бонусов",
                    projected_bonus_amount=guest_preview.projected_bonus_amount,
                ),
            ]
        )
        if can_redeem:
            lines.append(_(
                "Доступно списать: {redeemable_bonus_amount} бонусов",
                redeemable_bonus_amount=guest_preview.redeemable_bonus_amount,
            ))
            if redeem_active:
                net_amount = (
                    guest_preview.total_amount - guest_preview.redeemable_bonus_amount
                )
                lines.append(
                    _(
                        "Списываем бонусами: {redeemable_bonus_amount} • к оплате: {net_amount} "
                        "грн",
                        redeemable_bonus_amount=guest_preview.redeemable_bonus_amount,
                        net_amount=net_amount,
                    )
                )
    else:
        lines.append(_("Добавьте позиции из меню кнопками ниже."))

    return "\n".join(lines), keyboard, selected_category_id


async def _resolve_staff_employee_by_telegram_id(telegram_id: int, bot: Bot):
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    try:
        # Staff access is partner-scoped: the same Telegram account may work
        # with different venues, but only through an explicit employee binding.
        employee = await sync_to_async(get_staff_employee_by_telegram)(
            partner.id,
            telegram_id,
        )
    except EmployeeProfile.DoesNotExist as exc:
        raise OrderFlowError(
            _("Сотрудник не привязан к этому боту. Укажите Telegram account в профиле сотрудника.")
        ) from exc
    # Attach the partner's bot modules so staff-side capabilities can be gated by
    # the enabled modules, not only by the employee role.
    employee.bot_content = await sync_to_async(BotContent.for_partner)(partner)
    return partner, employee


async def _resolve_staff_employee(message: Message, bot: Bot):
    return await _resolve_staff_employee_by_telegram_id(message.from_user.id, bot)


async def _resolve_staff_employee_from_callback(callback: CallbackQuery, bot: Bot):
    return await _resolve_staff_employee_by_telegram_id(callback.from_user.id, bot)


async def _send_billing_feed(
    message: Message,
    *,
    partner_id,
    can_quick_sale: bool,
    can_view_day_report: bool,
    actor_user=None,
) -> None:
    requests, bills, unbilled_orders = await sync_to_async(
        _load_billing_feed_data
    )(partner_id)
    is_refreshing_existing_overview = _is_staff_overview_message(message, _("Счета и оплаты"))
    overview_text = _render_billing_overview(requests, bills, unbilled_orders)
    overview_markup = build_staff_billing_overview_keyboard(
        unbilled_order_shortcuts=_build_unbilled_order_shortcuts(unbilled_orders),
        can_quick_sale=can_quick_sale,
        can_view_day_report=can_view_day_report,
    )
    if is_refreshing_existing_overview:
        await _safe_edit_message_text(
            message,
            overview_text,
            reply_markup=overview_markup,
        )
    else:
        await message.answer(
            overview_text,
            reply_markup=overview_markup,
        )
    if is_refreshing_existing_overview:
        return
    for billing_request in requests:
        await message.answer(
            _render_billing_request_card(billing_request),
            reply_markup=build_staff_billing_request_actions_keyboard(
                request_id=str(billing_request.id),
                bill_public_id=billing_request.bill.public_id if billing_request.bill_id else None,
                can_process_request=(
                    billing_request.request_type == BillingRequest.RequestType.CUSTOM_SPLIT
                ),
            ),
        )
    for bill in bills:
        bill_text, bill_markup = await sync_to_async(_build_bill_card_message)(bill, actor_user)
        await message.answer(bill_text, reply_markup=bill_markup)


def _load_billing_feed_data(partner_id):
    return (
        list(BillingRequestRepository.open_for_partner(partner_id)),
        list(BillRepository.open_for_partner(partner_id)),
        list(BillingOperationsRepository.unbilled_open_orders_for_partner(partner_id)),
    )


async def _send_tables_feed(
    message: Message,
    *,
    partner_id,
    can_quick_sale: bool,
    can_view_day_report: bool,
) -> None:
    active_sessions, unbilled_orders, visit_counts = await sync_to_async(
        _load_tables_feed_data
    )(partner_id)
    await message.answer(
        _render_tables_overview(active_sessions, unbilled_orders),
        reply_markup=build_staff_tables_overview_keyboard(
            table_shortcuts=_build_active_table_shortcuts(
                active_sessions,
                unbilled_orders,
                visit_counts,
            ),
            can_quick_sale=can_quick_sale,
            can_view_day_report=can_view_day_report,
        ),
    )


def _load_tables_feed_data(partner_id):
    visit_counts: dict[str, tuple[int, int]] = {}
    for row in (
        TableVisit.objects.filter(partner_id=partner_id, status=TableVisit.Status.ACTIVE)
        .values("table_id", "table__number")
        .annotate(active_visits=Count("id"))
    ):
        visit_counts[str(row["table_id"])] = (row["table__number"], row["active_visits"])
    return (
        list(BillingOperationsRepository.active_sessions_for_partner(partner_id)),
        list(BillingOperationsRepository.unbilled_open_orders_for_partner(partner_id)),
        visit_counts,
    )


def _build_table_ops_view(*, partner_id, table_id):
    table = Table.objects.get(partner_id=partner_id, id=table_id)
    active_sessions = list(
        BillingOperationsRepository.active_sessions_for_partner(partner_id).filter(table_id=table_id)
    )
    open_orders = list(OrderRepository.open_for_partner(partner_id).filter(table_id=table_id))
    unbilled_orders = list(
        BillingOperationsRepository.unbilled_open_orders_for_partner(partner_id)
        .filter(table_id=table_id)
        .select_related("table_session")
    )
    open_bills = list(BillRepository.open_for_partner(partner_id).filter(table_id=table_id))
    visits = _table_visit_summaries(
        partner_id=partner_id,
        table_id=table_id,
        active_sessions=active_sessions,
        unbilled_orders=unbilled_orders,
        open_bills=open_bills,
    )
    legacy_unbilled = [
        order
        for order in unbilled_orders
        if order.table_session_id is None or order.table_session.visit_id is None
    ]
    text = _render_table_card(
        table=table,
        active_sessions=active_sessions,
        open_orders=open_orders,
        unbilled_orders=unbilled_orders,
        open_bills=open_bills,
        visits=visits,
    )
    keyboard = build_staff_table_actions_keyboard(
        table_id=str(table.id),
        # Table-level buttons bill orders without a visit (created before visits).
        can_create_shared_bill=bool(legacy_unbilled) and not visits,
        can_create_personal_bills=bool(legacy_unbilled) and not visits,
        session_actions=_build_session_close_actions(active_sessions),
        visit_actions=[
            (summary["id"], summary["title"], summary["unbilled"] > 0) for summary in visits
        ],
    )
    return text, keyboard


def _table_visit_summaries(*, partner_id, table_id, active_sessions, unbilled_orders, open_bills):
    summaries = []
    visits = TableVisit.objects.filter(
        partner_id=partner_id,
        table_id=table_id,
        status=TableVisit.Status.ACTIVE,
    ).order_by("started_at")
    for index, visit in enumerate(visits, start=1):
        started = timezone.localtime(visit.started_at).strftime("%H:%M")
        summaries.append(
            {
                "id": str(visit.id),
                "title": _("Визит {index} (с {started})", index=index, started=started),
                "guests": sum(1 for session in active_sessions if session.visit_id == visit.id),
                "unbilled": sum(
                    1
                    for order in unbilled_orders
                    if order.table_session_id and order.table_session.visit_id == visit.id
                ),
                "open_bills": sum(1 for bill in open_bills if bill.visit_id == visit.id),
            }
        )
    return summaries


async def _render_staff_sale_message(
    *,
    bot: Bot,
    chat_id: int,
    message_id: int,
    partner_id,
    customer_code: str,
    items_map: dict[str, int],
    selected_category_id: str | None,
    comment: str = "",
    redeem_bonus: bool = False,
) -> tuple[str, int, int]:
    text, keyboard, selected_category_id = await sync_to_async(_compose_staff_sale_view)(
        partner_id=partner_id,
        customer_code=customer_code,
        items_map=items_map,
        selected_category_id=selected_category_id,
        comment=comment,
        redeem_bonus=redeem_bonus,
    )
    try:
        await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=keyboard,
        )
        return selected_category_id, chat_id, message_id
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if "message is not modified" in error_text:
            return selected_category_id, chat_id, message_id
        if "message to edit not found" in error_text or "message can't be edited" in error_text:
            sent_message = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
            )
            return selected_category_id, sent_message.chat.id, sent_message.message_id
        raise


async def _render_notifications_message(callback_or_message, partner_id, employee_id) -> None:
    notifications = await sync_to_async(list)(
        StaffNotificationRepository.unread_for_employee(employee_id)[:10]
    )
    markup = build_staff_notifications_keyboard() if notifications else None
    text = _render_staff_notifications(notifications)
    should_send_cards = True

    if isinstance(callback_or_message, CallbackQuery):
        should_send_cards = not _is_staff_overview_message(
            callback_or_message.message,
            _("Уведомления персоналу"),
        )
        await _safe_edit_message_text(
            callback_or_message.message,
            text,
            reply_markup=markup,
        )
    else:
        await callback_or_message.answer(text, reply_markup=markup)
    if not should_send_cards:
        return
    for notification in notifications:
        target = (
            callback_or_message.message
            if isinstance(callback_or_message, CallbackQuery)
            else callback_or_message
        )
        await target.answer(
            _render_staff_notification_card(notification),
            reply_markup=build_staff_notification_item_keyboard(
                notification_id=str(notification.id),
                category=(
                    notification.category
                    if not notification.order_id or notification.order.status == Order.Status.NEW
                    else StaffNotification.Category.ORDER_STATUS_CHANGED
                ),
                order_public_id=notification.order.public_id if notification.order_id else None,
            ),
        )


@router.message(Command("staff"))
async def staff_help_handler(message: Message, bot: Bot) -> None:
    try:
        _partner, employee = await _resolve_staff_employee(message, bot)
    except OrderFlowError:
        # Guests are not staff: stay completely silent so /staff reveals nothing.
        return

    await message.answer(
        (
            _(
                "<b>Режим персонала активирован</b>\n"
                "Выберите действие кнопками ниже.\n"
                "Команды можно использовать как резервный вариант, но основной режим уже "
                "кнопочный.",
            )
        ),
        reply_markup=build_staff_home_keyboard(
            can_quick_sale=_can_use_quick_sale(employee),
            can_view_day_report=_can_view_day_report(employee),
            can_manage_billing=_can_manage_billing(employee),
            can_view_orders=_can_view_open_orders(employee),
            can_view_tables=_can_view_tables(employee),
        ),
    )


@router.callback_query(lambda c: c.data == "stafforders:refresh")
async def staff_orders_refresh_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_open_orders_allowed(employee)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    orders = await sync_to_async(list)(get_open_orders_for_partner(partner.id))
    updated = await _safe_edit_message_text(
        callback.message,
        _render_open_orders_summary(orders),
        reply_markup=build_staff_orders_overview_keyboard(
            order_shortcuts=_build_order_shortcuts(orders),
            can_quick_sale=_can_use_quick_sale(employee),
            can_view_day_report=_can_view_day_report(employee),
            can_manage_billing=_can_manage_billing(employee),
            can_view_tables=_can_view_tables(employee),
        ),
    )
    await callback.answer(_("Список обновлён") if updated else _("Без изменений"))


@router.callback_query(lambda c: c.data and c.data.startswith("stafforderopen:"))
async def staff_order_open_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_open_orders_allowed(employee)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    _unused, order_public_id = (callback.data or "").split(":", 1)
    try:
        order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            order_public_id,
        )
    except Order.DoesNotExist:
        await callback.answer(_("Заказ не найден."), show_alert=True)
        return

    await callback.message.answer(
        _render_order_card(order),
        reply_markup=_order_actions_keyboard(order),
    )
    await callback.answer(_("Открываю заказ"))


@router.callback_query(lambda c: c.data == "staffbill:list")
async def staff_billing_refresh_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_manage_billing(employee):
            raise OrderFlowError(
                _("Работа со счетами доступна только владельцу, менеджеру или кассиру.")
            )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await _send_billing_feed(
        callback.message,
        partner_id=partner.id,
        can_quick_sale=_can_use_quick_sale(employee),
        can_view_day_report=_can_view_day_report(employee),
        actor_user=employee.user,
    )
    await callback.answer(_("Счета обновлены"))


@router.callback_query(lambda c: c.data == "stafftables:refresh")
async def staff_tables_refresh_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_view_tables(employee):
            raise OrderFlowError(
                _(
                    "Работа со столами доступна только владельцу, менеджеру или кассиру при "
                    "включённом модуле столов.",
                )
            )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await _send_tables_feed(
        callback.message,
        partner_id=partner.id,
        can_quick_sale=_can_use_quick_sale(employee),
        can_view_day_report=_can_view_day_report(employee),
    )
    await callback.answer(_("Столы обновлены"))


@router.callback_query(lambda c: c.data and c.data.startswith("stafftableopen:"))
async def staff_table_open_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_view_tables(employee):
            raise OrderFlowError(
                _(
                    "Работа со столами доступна только владельцу, менеджеру или кассиру при "
                    "включённом модуле столов.",
                )
            )
        _unused, table_id = (callback.data or "").split(":", 1)
        text, keyboard = await sync_to_async(_build_table_ops_view)(
            partner_id=partner.id,
            table_id=table_id,
        )
    except Table.DoesNotExist:
        await callback.answer(_("Стол не найден."), show_alert=True)
        return
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer(_("Открываю стол"))


@router.callback_query(lambda c: c.data and c.data.startswith("staffsessionclose:"))
async def staff_session_close_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_view_tables(employee):
            raise OrderFlowError(
                _(
                    "Закрытие сессий доступно только владельцу, менеджеру или кассиру при "
                    "включённом модуле столов.",
                )
            )
        _unused, session_id = (callback.data or "").split(":", 1)
        session = await sync_to_async(
            lambda: BillingOperationsRepository.active_sessions_for_partner(
                partner.id
            ).get(id=session_id)
        )()
        table_id = session.table_id
        await sync_to_async(close_table_session_if_settled)(session)
        text, keyboard = await sync_to_async(_build_table_ops_view)(
            partner_id=partner.id,
            table_id=table_id,
        )
    except TableSessionError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except TableSession.DoesNotExist:
        await callback.answer(_("Сессия уже закрыта или не найдена."), show_alert=True)
        return
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await _safe_edit_message_text(callback.message, text, reply_markup=keyboard)
    await callback.answer(_("Сессия закрыта"))


@router.callback_query(lambda c: c.data and c.data.startswith("stafftablebillshared:"))
async def staff_table_shared_bill_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_manage_billing(employee):
            raise OrderFlowError(
                _("Работа со столами и счетами доступна только владельцу, менеджеру или кассиру.")
            )
        _unused, table_id = (callback.data or "").split(":", 1)
        await sync_to_async(create_shared_bill_for_table)(
            partner_id=partner.id,
            table_id=table_id,
        )
        text, keyboard = await sync_to_async(_build_table_ops_view)(
            partner_id=partner.id,
            table_id=table_id,
        )
    except BillingServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await _safe_edit_message_text(callback.message, text, reply_markup=keyboard)
    await callback.answer(_("Общий счёт подготовлен"))


@router.callback_query(lambda c: c.data and c.data.startswith("stafftablebillpersonal:"))
async def staff_table_personal_bills_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_manage_billing(employee):
            raise OrderFlowError(
                _("Работа со столами и счетами доступна только владельцу, менеджеру или кассиру.")
            )
        _unused, table_id = (callback.data or "").split(":", 1)
        await sync_to_async(create_personal_bills_for_table)(
            partner_id=partner.id,
            table_id=table_id,
        )
        text, keyboard = await sync_to_async(_build_table_ops_view)(
            partner_id=partner.id,
            table_id=table_id,
        )
    except BillingServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await _safe_edit_message_text(callback.message, text, reply_markup=keyboard)
    await callback.answer(_("Персональные счета подготовлены"))


def _load_staff_visit(*, partner_id, raw_id: str) -> TableVisit:
    try:
        visit_id = UUID(hex=raw_id)
    except ValueError as exc:
        raise TableVisit.DoesNotExist from exc
    return TableVisit.objects.select_related("table").get(partner_id=partner_id, id=visit_id)


@router.callback_query(
    lambda c: c.data and c.data.split(":", 1)[0] in {"stafftvshared", "stafftvpersonal"}
)
async def staff_visit_bill_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_manage_billing(employee):
            raise OrderFlowError(
                _("Работа со столами и счетами доступна только владельцу, менеджеру или кассиру.")
            )
        prefix, raw_visit_id = (callback.data or "").split(":", 1)
        visit = await sync_to_async(_load_staff_visit)(partner_id=partner.id, raw_id=raw_visit_id)
        if visit.status != TableVisit.Status.ACTIVE:
            raise OrderFlowError(_("Этот визит уже закрыт."))
        builder = (
            create_shared_bill_for_table
            if prefix == "stafftvshared"
            else create_personal_bills_for_table
        )
        await sync_to_async(builder)(
            partner_id=partner.id,
            table_id=visit.table_id,
            visit_id=visit.id,
        )
        text, keyboard = await sync_to_async(_build_table_ops_view)(
            partner_id=partner.id,
            table_id=visit.table_id,
        )
    except TableVisit.DoesNotExist:
        await callback.answer(_("Визит не найден."), show_alert=True)
        return
    except (BillingServiceError, OrderFlowError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await _safe_edit_message_text(callback.message, text, reply_markup=keyboard)
    await callback.answer(_("Счёт визита подготовлен"))


@router.callback_query(lambda c: c.data and c.data.startswith("stafftvclose:"))
async def staff_visit_close_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_view_tables(employee):
            raise OrderFlowError(
                _(
                    "Закрытие визитов доступно только владельцу, менеджеру или кассиру при "
                    "включённом модуле столов.",
                )
            )
        _unused, raw_visit_id = (callback.data or "").split(":", 1)
        visit = await sync_to_async(_load_staff_visit)(partner_id=partner.id, raw_id=raw_visit_id)
        await sync_to_async(close_table_visit)(visit, actor=employee.user)
        text, keyboard = await sync_to_async(_build_table_ops_view)(
            partner_id=partner.id,
            table_id=visit.table_id,
        )
    except TableVisit.DoesNotExist:
        await callback.answer(_("Визит не найден."), show_alert=True)
        return
    except (TableSessionError, OrderFlowError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await _safe_edit_message_text(callback.message, text, reply_markup=keyboard)
    await callback.answer(_("Визит закрыт"))


@router.callback_query(lambda c: c.data == "staffreport:today")
async def staff_report_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_view_day_report(employee):
            raise OrderFlowError(
                _("Отчёт дня сейчас доступен только владельцу, менеджеру или кассиру.")
            )
        report = await sync_to_async(build_daily_operations_report)(partner_id=partner.id)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    updated = await _safe_edit_message_text(
        callback.message,
        _render_day_report(report),
        reply_markup=build_staff_report_summary_keyboard(),
    )
    await callback.answer(_("Отчёт обновлён") if updated else _("Без изменений"))


@router.callback_query(lambda c: c.data and c.data.startswith("staffreportsec:"))
async def staff_report_section_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_view_day_report(employee):
            raise OrderFlowError(
                _("Отчёт дня сейчас доступен только владельцу, менеджеру или кассиру.")
            )
        _prefix, section, raw_page = (callback.data or "").split(":", 2)
        page = int(raw_page)
        builder_map = {
            "bills": build_daily_paid_bills_page,
            "walkins": build_daily_walk_in_sales_page,
            "tables": build_daily_tables_page,
            "tails": build_daily_tails_page,
        }
        builder = builder_map.get(section)
        if builder is None:
            raise OrderFlowError(_("Неизвестный раздел журнала дня."))
        page_result = await sync_to_async(builder)(partner_id=partner.id, page=page)
    except ValueError:
        await callback.answer(_("Некорректная страница журнала."), show_alert=True)
        return
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    updated = await _safe_edit_message_text(
        callback.message,
        _render_report_section(page_result),
        reply_markup=build_staff_report_section_keyboard(
            section=section,
            page=page_result.page,
            total_pages=page_result.total_pages,
            item_shortcuts=_build_report_section_shortcuts(page_result),
        ),
    )
    await callback.answer(_("Раздел журнала открыт") if updated else _("Без изменений"))


@router.callback_query(lambda c: c.data and c.data.startswith("staffreportsaleopen:"))
async def staff_report_walk_in_sale_open_callback_handler(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_view_day_report(employee):
            raise OrderFlowError(
                _("Отчёт дня сейчас доступен только владельцу, менеджеру или кассиру.")
            )
        _prefix, sale_id, raw_page = (callback.data or "").split(":", 2)
        sale = await sync_to_async(
            lambda: WalkInSale.objects.select_related("created_by").prefetch_related("items").get(
                partner_id=partner.id,
                id=sale_id,
            )
        )()
        page = int(raw_page)
    except WalkInSale.DoesNotExist:
        await callback.answer(_("Продажа не найдена."), show_alert=True)
        return
    except ValueError:
        await callback.answer(_("Некорректная ссылка на продажу."), show_alert=True)
        return
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(
        _render_walk_in_sale_card(sale),
        reply_markup=build_staff_report_walkin_detail_keyboard(
            section="walkins",
            page=page,
        ),
    )
    await callback.answer(_("Карточка продажи открыта"))


@router.callback_query(lambda c: c.data and c.data.startswith("staffreporttableopen:"))
async def staff_report_table_open_callback_handler(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_view_day_report(employee):
            raise OrderFlowError(
                _("Отчёт дня сейчас доступен только владельцу, менеджеру или кассиру.")
            )
        _prefix, table_id, raw_page = (callback.data or "").split(":", 2)
        page = int(raw_page)
        detail = await sync_to_async(build_daily_table_detail)(
            partner_id=partner.id,
            table_id=table_id,
        )
    except Table.DoesNotExist:
        await callback.answer(_("Стол не найден."), show_alert=True)
        return
    except ValueError:
        await callback.answer(_("Некорректная ссылка на стол."), show_alert=True)
        return
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(
        _render_report_table_detail(detail),
        reply_markup=build_staff_report_table_detail_keyboard(page=page),
    )
    await callback.answer(_("Карточка стола открыта"))


@router.callback_query(lambda c: c.data and c.data.startswith("stafforder:"))
async def staff_order_status_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_open_orders_allowed(employee)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    _unused, order_public_id, to_status = (callback.data or "").split(":", 2)
    try:
        order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            order_public_id,
        )
        previous_status = order.status
        if to_status == Order.Status.CANCELED:
            # A billed order is cancelled through its bill card (PQ-028).
            order = await sync_to_async(cancel_order)(
                order=order,
                actor=employee.user,
                note="Cancelled via staff inline button",
            )
        else:
            order = await sync_to_async(transition_order_status)(
                order=order,
                to_status=to_status,
                actor_user=employee.user,
                note="Updated via staff inline button",
            )
    except Order.DoesNotExist:
        await callback.answer(_("Заказ не найден."), show_alert=True)
        return
    except BillingServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except OrderFlowError:
        refreshed_order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            order_public_id,
        )
        await _safe_edit_message_text(
            callback.message,
            _render_order_card(refreshed_order),
            reply_markup=_order_actions_keyboard(refreshed_order),
        )
        if refreshed_order.status != previous_status:
            await callback.answer(_("Заказ уже обновлён, показываю актуальный статус"))
        else:
            await callback.answer(_("Это действие для заказа больше недоступно"), show_alert=True)
        return

    refreshed_order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
        partner.id,
        order.public_id,
    )
    await _safe_edit_message_text(
        callback.message,
        _render_order_card(refreshed_order),
        reply_markup=_order_actions_keyboard(refreshed_order),
    )
    labels = _status_labels(refreshed_order)
    await callback.answer(
        _("Статус: {status}", status=_(labels[to_status]) if to_status in labels else to_status)
    )


@router.callback_query(lambda c: c.data and c.data.startswith("stafforderrefresh:"))
async def staff_order_refresh_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_open_orders_allowed(employee)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    _unused, order_public_id = (callback.data or "").split(":", 1)
    try:
        order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            order_public_id,
        )
    except Order.DoesNotExist:
        await callback.answer(_("Заказ не найден."), show_alert=True)
        return

    updated = await _safe_edit_message_text(
        callback.message,
        _render_order_card(order),
        reply_markup=_order_actions_keyboard(order),
    )
    await callback.answer(_("Карточка обновлена") if updated else _("Без изменений"))


@router.callback_query(lambda c: c.data and c.data.startswith("stafftx"))
async def staff_transfer_check_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    """Pickup prepayment check: confirm, ask to confirm a rejection, reject."""

    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_open_orders_allowed(employee)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    action, _unused, order_public_id = (callback.data or "").partition(":")
    if action not in {"stafftxok", "stafftxno", "stafftxnoy"} or not order_public_id:
        await callback.answer(_("Неизвестное действие."), show_alert=True)
        return
    try:
        order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            order_public_id,
        )
    except Order.DoesNotExist:
        await callback.answer(_("Заказ не найден."), show_alert=True)
        return

    if action == "stafftxno":
        if not awaits_transfer_check(order):
            await callback.answer(_("Оплата по этому заказу уже обработана."), show_alert=True)
            return
        await _safe_edit_message_text(
            callback.message,
            _render_order_card(order)
            + _(
                "\n"
                "\n"
                "<b>Отменить заказ, потому что оплата не поступила?</b> Списанные бонусы "
                "вернутся гостю, гость получит сообщение.",
            ),
            reply_markup=build_staff_transfer_reject_confirm_keyboard(order.public_id),
        )
        await callback.answer()
        return

    service = confirm_transfer if action == "stafftxok" else reject_transfer
    try:
        await sync_to_async(service)(order=order, actor=employee.user)
    except (RemoteOrderError, BillingServiceError, OrderFlowError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    refreshed_order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
        partner.id,
        order_public_id,
    )
    await _safe_edit_message_text(
        callback.message,
        _render_order_card(refreshed_order),
        reply_markup=_order_actions_keyboard(refreshed_order),
    )
    await callback.answer(
        _("Оплата подтверждена, заказ в работе.")
        if action == "stafftxok"
        else _("Заказ отменён: оплата не поступила.")
    )


@router.callback_query(lambda c: c.data and c.data.startswith("staffrc"))
async def staff_receipt_payment_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    """Paid on receipt (PQ-033): cash, card, or the guest did not take it."""

    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_open_orders_allowed(employee)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    action, _unused, order_public_id = (callback.data or "").partition(":")
    methods = {
        "staffrccash": Payment.Method.CASH,
        "staffrccard": Payment.Method.TERMINAL,
        "staffrccod": Payment.Method.COD,
    }
    if action not in {*methods, "staffrcno", "staffrcnoy"} or not order_public_id:
        await callback.answer(_("Неизвестное действие."), show_alert=True)
        return
    try:
        order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            order_public_id,
        )
    except Order.DoesNotExist:
        await callback.answer(_("Заказ не найден."), show_alert=True)
        return

    if action == "staffrcno":
        if not awaits_payment_on_receipt(order):
            await callback.answer(_("Оплата по этому заказу уже обработана."), show_alert=True)
            return
        await _safe_edit_message_text(
            callback.message,
            _render_order_card(order)
            + (
                _("\n\n<b>Посылку не забрали и деньги не пришли — отменить заказ?</b> ")
                if order.channel == Order.Channel.SHIPPING
                else _("\n\n<b>Гость не забрал заказ и не заплатил — отменить заказ?</b> ")
            )
            + _("Списанные бонусы вернутся гостю."),
            reply_markup=build_staff_receipt_refusal_confirm_keyboard(order.public_id),
        )
        await callback.answer()
        return

    try:
        if action in methods:
            await sync_to_async(record_payment_on_receipt)(
                order=order, actor=employee.user, method=methods[action]
            )
        else:
            await sync_to_async(cancel_unpaid_remote_order)(order=order, actor=employee.user)
    except (RemoteOrderError, BillingServiceError, OrderFlowError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    refreshed_order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
        partner.id,
        order_public_id,
    )
    await _safe_edit_message_text(
        callback.message,
        _render_order_card(refreshed_order),
        reply_markup=_order_actions_keyboard(refreshed_order),
    )
    await callback.answer(
        _("Оплата при получении отмечена.") if action in methods else _("Заказ отменён.")
    )


def _is_tracking_answer(message: Message) -> bool:
    replied = message.reply_to_message
    return bool(
        message.text
        and replied is not None
        and replied.from_user is not None
        and replied.from_user.is_bot
        and any((replied.text or "").startswith(prompt) for prompt in variants(TRACKING_PROMPT))
    )


@router.callback_query(lambda c: c.data and c.data.startswith("staffship:"))
async def staff_ship_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    """Shipping (PQ-034): «Отправлен» — ask for the ТТН in a reply."""

    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_open_orders_allowed(employee)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    _unused, _unused, order_public_id = (callback.data or "").partition(":")
    try:
        order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            order_public_id,
        )
    except Order.DoesNotExist:
        await callback.answer(_("Заказ не найден."), show_alert=True)
        return
    if order.channel != Order.Channel.SHIPPING or order.status != Order.Status.READY:
        await callback.answer(_("Это действие для заказа больше недоступно."), show_alert=True)
        return
    await callback.message.answer(
        _(
            "{tracking_prompt} #{public_id}\n"
            "{shipping_carrier}: {shipping_destination}\n"
            "Напишите номер ТТН в ответ на это сообщение.",
            tracking_prompt=_(TRACKING_PROMPT),
            public_id=order.public_id,
            shipping_carrier=escape_html(order.get_shipping_carrier_display()),
            shipping_destination=escape_html(order.shipping_destination),
        ),
        reply_markup=ForceReply(input_field_placeholder=_("Номер ТТН")),
    )
    await callback.answer()


@router.message(_is_tracking_answer)
async def staff_tracking_number_handler(message: Message, bot: Bot) -> None:
    """A staff reply to «ТТН для заказа #…»: keep the number, mark it sent."""

    try:
        partner, employee = await _resolve_staff_employee(message, bot)
        _ensure_open_orders_allowed(employee)
    except OrderFlowError as exc:
        await message.answer(str(exc))
        return
    match = re.search(r"#([0-9a-f]+)", message.reply_to_message.text or "")
    if match is None:
        await message.answer(_("Не нашёл номер заказа — нажмите «Отправлен» в карточке ещё раз."))
        return
    try:
        order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            match.group(1),
        )
        order = await sync_to_async(mark_order_shipped)(
            order=order, actor=employee.user, tracking_number=message.text
        )
    except Order.DoesNotExist:
        await message.answer(_("Заказ не найден."))
        return
    except (RemoteOrderError, OrderFlowError) as exc:
        await message.answer(str(exc))
        return
    refreshed_order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
        partner.id,
        order.public_id,
    )
    await message.answer(
        _render_order_card(refreshed_order),
        reply_markup=_order_actions_keyboard(refreshed_order),
    )


@router.callback_query(lambda c: c.data == "staffnotif:refresh")
async def staff_notifications_refresh_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await _render_notifications_message(callback, partner.id, employee.id)
    await callback.answer(_("Уведомления обновлены"))


@router.callback_query(lambda c: c.data == "staffnotif:readall")
async def staff_notifications_read_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    updated_count = await sync_to_async(mark_staff_notifications_read)(
        employee_id=employee.id,
        partner_id=partner.id,
    )
    await _render_notifications_message(callback, partner.id, employee.id)
    await callback.answer(_(
        "Отмечено как прочитанные: {updated_count}",
        updated_count=updated_count,
    ))


@router.callback_query(lambda c: c.data and c.data.startswith("staffnotifread:"))
async def staff_notification_read_one_callback_handler(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _unused, notification_id = (callback.data or "").split(":", 1)
        updated = await sync_to_async(mark_staff_notification_read)(
            notification_id=notification_id,
            employee_id=employee.id,
            partner_id=partner.id,
        )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if updated:
        updated = await _safe_edit_message_text(
            callback.message,
            _(
                "{html_text}\n"
                "\n"
                "<i>Помечено как прочитанное.</i>",
                html_text=callback.message.html_text,
            ),
            reply_markup=None,
        )
        await callback.answer(_("Уведомление отмечено") if updated else _("Без изменений"))
        return
    await callback.answer(_("Уведомление уже было отмечено"))


@router.callback_query(lambda c: c.data and c.data.startswith("staffnotiforder:"))
async def staff_notification_open_order_callback_handler(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_open_orders_allowed(employee)
        _unused, order_public_id = (callback.data or "").split(":", 1)
        order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            order_public_id,
        )
    except Order.DoesNotExist:
        await callback.answer(_("Заказ не найден."), show_alert=True)
        return
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(
        _render_order_card(order),
        reply_markup=_order_actions_keyboard(order),
    )
    await callback.answer(_("Открываю заказ"))


@router.callback_query(lambda c: c.data and c.data.startswith("staffnotifaccept:"))
async def staff_notification_accept_order_callback_handler(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_open_orders_allowed(employee)
        _unused, notification_id, order_public_id = (callback.data or "").split(":", 2)
        order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            order_public_id,
        )
    except Order.DoesNotExist:
        await callback.answer(_("Заказ не найден."), show_alert=True)
        return
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if order.status != Order.Status.NEW:
        await sync_to_async(mark_staff_notification_read)(
            notification_id=notification_id,
            employee_id=employee.id,
            partner_id=partner.id,
        )
        refreshed_order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            order.public_id,
        )
        await callback.message.answer(
            _render_order_card(refreshed_order),
            reply_markup=_order_actions_keyboard(refreshed_order),
        )
        await callback.answer(_("Заказ уже в работе, показываю актуальную карточку"))
        return

    updated = await sync_to_async(mark_staff_notification_read)(
        notification_id=notification_id,
        employee_id=employee.id,
        partner_id=partner.id,
    )
    try:
        order = await sync_to_async(transition_order_status)(
            order=order,
            to_status=Order.Status.ACCEPTED,
            actor_user=employee.user,
            note="Accepted from staff notification card",
        )
    except OrderFlowError:
        await sync_to_async(mark_staff_notification_read)(
            notification_id=notification_id,
            employee_id=employee.id,
            partner_id=partner.id,
        )
        refreshed_order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            order.public_id,
        )
        await callback.message.answer(
            _render_order_card(refreshed_order),
            reply_markup=_order_actions_keyboard(refreshed_order),
        )
        await callback.answer(_("Заказ уже изменился, показываю актуальную карточку"))
        return

    refreshed_order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
        partner.id,
        order.public_id,
    )
    await callback.message.answer(
        _render_order_card(refreshed_order),
        reply_markup=_order_actions_keyboard(refreshed_order),
    )
    if updated:
        await _safe_edit_message_text(
            callback.message,
            _("{html_text}\n\n<i>Заказ взят в работу.</i>", html_text=callback.message.html_text),
            reply_markup=None,
        )
    await callback.answer(_("Заказ взят в работу"))


@router.callback_query(lambda c: c.data and c.data.startswith("staffbillopen:"))
@router.callback_query(lambda c: c.data and c.data.startswith("staffbillrefresh:"))
async def staff_bill_open_or_refresh_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_manage_billing(employee):
            raise OrderFlowError(
                _("Работа со счетами доступна только владельцу, менеджеру или кассиру.")
            )
        _unused, bill_public_id = (callback.data or "").split(":", 1)
        bill = await sync_to_async(BillRepository.by_public_id_for_partner)(
            partner.id,
            bill_public_id,
        )
    except Bill.DoesNotExist:
        await callback.answer(_("Счёт не найден."), show_alert=True)
        return
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    bill_text, bill_markup = await sync_to_async(_build_bill_card_message)(bill, employee.user)
    updated = await _safe_edit_message_text(callback.message, bill_text, reply_markup=bill_markup)
    await callback.answer(_("Карточка счёта обновлена") if updated else _("Без изменений"))


@router.callback_query(lambda c: c.data and c.data.startswith("staffbillissue:"))
async def staff_bill_issue_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_manage_billing(employee):
            raise OrderFlowError(
                _("Работа со счетами доступна только владельцу, менеджеру или кассиру.")
            )
        _unused, bill_public_id = (callback.data or "").split(":", 1)
        bill = await sync_to_async(BillRepository.by_public_id_for_partner)(
            partner.id,
            bill_public_id,
        )
        bill = await sync_to_async(issue_bill)(bill)
    except Bill.DoesNotExist:
        await callback.answer(_("Счёт не найден."), show_alert=True)
        return
    except (OrderFlowError, BillingServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    refreshed_bill = await sync_to_async(BillRepository.by_public_id_for_partner)(
        partner.id,
        bill.public_id,
    )
    bill_text, bill_markup = await sync_to_async(_build_bill_card_message)(
        refreshed_bill,
        employee.user,
    )
    await _safe_edit_message_text(callback.message, bill_text, reply_markup=bill_markup)
    await callback.answer(_("Счёт выдан"))


@router.callback_query(lambda c: c.data and c.data.startswith("staffbillpaycash:"))
@router.callback_query(lambda c: c.data and c.data.startswith("staffbillpayterminal:"))
@router.callback_query(lambda c: c.data and c.data.startswith("staffbillpaymixed:"))
@router.callback_query(lambda c: c.data and c.data.startswith("staffbillpaybonus:"))
@router.callback_query(lambda c: c.data and c.data.startswith("staffbillredeem:"))
async def staff_bill_pay_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    notice = _("Оплата зафиксирована")
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_manage_billing(employee):
            raise OrderFlowError(
                _("Работа со счетами доступна только владельцу, менеджеру или кассиру.")
            )
        prefix, bill_public_id = (callback.data or "").split(":", 1)
        bill = await sync_to_async(BillRepository.by_public_id_for_partner)(
            partner.id,
            bill_public_id,
        )
        if prefix in {"staffbillpaybonus", "staffbillredeem"}:
            bonus_result = await sync_to_async(pay_bill_with_bonuses)(
                bill=bill,
                created_by=employee.user,
                comment="Paid via staff bot with bonus balance.",
            )
            if bonus_result.remaining_amount > 0:
                notice = (
                    _(
                        "Списано бонусами: {bonus_spent_amount} грн. Остаток к оплате: "
                        "{remaining_amount} грн.",
                        bonus_spent_amount=bonus_result.bonus_spent_amount,
                        remaining_amount=bonus_result.remaining_amount,
                    )
                )
            else:
                notice = _("Счёт полностью оплачен бонусами")
        else:
            method = {
                "staffbillpaycash": Payment.Method.CASH,
                "staffbillpayterminal": Payment.Method.TERMINAL,
                "staffbillpaymixed": Payment.Method.MIXED,
            }[prefix]
            remaining_amount = (bill.total_amount - bill.paid_amount).quantize(
                Decimal("0.01")
            )
            await sync_to_async(record_payment)(
                bill=bill,
                amount=remaining_amount,
                method=method,
                created_by=employee.user,
                comment=f"Recorded via staff bot ({method}).",
            )
    except Bill.DoesNotExist:
        await callback.answer(_("Счёт не найден."), show_alert=True)
        return
    except (OrderFlowError, BillingServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    try:
        refreshed_bill = await sync_to_async(BillRepository.by_public_id_for_partner)(
            partner.id,
            bill_public_id,
        )
        text, markup = await sync_to_async(_build_bill_card_message)(
            refreshed_bill,
            employee.user,
        )
    except Bill.DoesNotExist:
        text = (
            _(
                "<b>Счёт #{bill_public_id}</b>\n"
                "Полностью оплачен и выведен из списка открытых счетов.",
                bill_public_id=bill_public_id,
            )
        )
        markup = None

    await _safe_edit_message_text(callback.message, text, reply_markup=markup)
    await callback.answer(notice)


@router.callback_query(lambda c: c.data and c.data.startswith("staffbillreqrefresh:"))
@router.callback_query(lambda c: c.data and c.data.startswith("staffbillreqprocess:"))
@router.callback_query(lambda c: c.data and c.data.startswith("staffbillreqcancel:"))
async def staff_billing_request_action_callback_handler(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_manage_billing(employee):
            raise OrderFlowError(
                _("Работа со счетами доступна только владельцу, менеджеру или кассиру.")
            )
        prefix, request_id = (callback.data or "").split(":", 1)
        billing_request = await sync_to_async(BillingRequestRepository.by_id_for_partner)(
            partner.id,
            request_id,
        )
        if prefix == "staffbillreqprocess":
            billing_request = await sync_to_async(mark_billing_request_processed)(billing_request)
        elif prefix == "staffbillreqcancel":
            billing_request = await sync_to_async(cancel_billing_request)(billing_request)
    except BillingRequest.DoesNotExist:
        await callback.answer(_("Запрос счёта не найден."), show_alert=True)
        return
    except (OrderFlowError, BillingServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    can_process_request = (
        billing_request.request_type == BillingRequest.RequestType.CUSTOM_SPLIT
        and billing_request.status
        in {BillingRequest.Status.OPEN, BillingRequest.Status.AUTO_PREPARED}
    )
    await _safe_edit_message_text(
        callback.message,
        _render_billing_request_card(billing_request),
        reply_markup=build_staff_billing_request_actions_keyboard(
            request_id=str(billing_request.id),
            bill_public_id=billing_request.bill.public_id if billing_request.bill_id else None,
            can_process_request=can_process_request,
        ),
    )
    await callback.answer(_("Запрос обновлён"))


@router.callback_query(lambda c: c.data == "staffsale:start")
async def staff_sale_start_callback_handler(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    try:
        _partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_quick_sale_allowed(employee)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.clear()
    await state.set_state(StaffQuickSaleStates.waiting_for_customer_code)
    await callback.message.answer(
        _(
            "Введите код клиента для быстрой продажи (например FE231A).\n"
            "Если клиент без кода — нажмите «Без кода (Аноним)» или отправьте «-».",
        ),
        reply_markup=build_staff_sale_code_prompt_keyboard(),
    )
    await callback.answer()


@router.message(StaffQuickSaleStates.waiting_for_customer_code)
async def staff_sale_customer_code_handler(
    message: Message,
    bot: Bot,
    state: FSMContext,
) -> None:
    customer_code = (message.text or "").strip().upper()
    # "-" is an explicit shortcut for an anonymous sale (no customer code).
    if customer_code == "-":
        customer_code = ""
    employee = None
    try:
        partner, employee = await _resolve_staff_employee(message, bot)
        _ensure_quick_sale_allowed(employee)
        text, keyboard, selected_category_id = await sync_to_async(_compose_staff_sale_view)(
            partner_id=partner.id,
            customer_code=customer_code,
            items_map={},
            selected_category_id=None,
        )
    except (OrderFlowError, BonusServiceError) as exc:
        await message.answer(
            str(exc),
            reply_markup=_build_staff_home_markup(employee),
        )
        return

    await state.set_state(StaffQuickSaleStates.browsing_menu)
    await state.update_data(
        customer_code=customer_code,
        items={},
        selected_category_id=selected_category_id,
        comment="",
        redeem_bonus=False,
    )
    sent_message = await message.answer(text, reply_markup=keyboard)
    await state.update_data(
        preview_chat_id=sent_message.chat.id,
        preview_message_id=sent_message.message_id,
    )


@router.callback_query(StaffQuickSaleStates.browsing_menu, lambda c: c.data == "staffsale:noop")
async def staff_sale_noop_handler(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(StaffQuickSaleStates.browsing_menu, lambda c: c.data == "staffsale:redeem")
async def staff_sale_redeem_toggle_handler(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    try:
        preview_data = _require_staff_sale_preview_state(data)
        new_redeem = not preview_data["redeem_bonus"]
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_quick_sale_allowed(employee)
        selected_category_id, preview_chat_id, preview_message_id = await _render_staff_sale_message(
            bot=bot,
            chat_id=preview_data["preview_chat_id"],
            message_id=preview_data["preview_message_id"],
            partner_id=partner.id,
            customer_code=preview_data["customer_code"],
            items_map=preview_data["items"],
            selected_category_id=preview_data["selected_category_id"],
            comment=preview_data["comment"],
            redeem_bonus=new_redeem,
        )
    except (OrderFlowError, BonusServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        redeem_bonus=new_redeem,
        selected_category_id=selected_category_id,
        preview_chat_id=preview_chat_id,
        preview_message_id=preview_message_id,
    )
    await callback.answer(_("Списываем бонусы") if new_redeem else _("Списание отменено"))


@router.callback_query(
    StaffQuickSaleStates.waiting_for_customer_code,
    lambda c: c.data == "staffsale:anon",
)
async def staff_sale_anonymous_handler(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_quick_sale_allowed(employee)
        text, keyboard, selected_category_id = await sync_to_async(_compose_staff_sale_view)(
            partner_id=partner.id,
            customer_code="",
            items_map={},
            selected_category_id=None,
        )
    except (OrderFlowError, BonusServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.set_state(StaffQuickSaleStates.browsing_menu)
    await state.update_data(
        customer_code="",
        items={},
        selected_category_id=selected_category_id,
        comment="",
        redeem_bonus=False,
    )
    sent_message = await callback.message.answer(text, reply_markup=keyboard)
    await state.update_data(
        preview_chat_id=sent_message.chat.id,
        preview_message_id=sent_message.message_id,
    )
    await callback.answer(_("Анонимная продажа"))


@router.callback_query(
    StaffQuickSaleStates.browsing_menu,
    lambda c: c.data == "staffsale:change_customer",
)
async def staff_sale_change_customer_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.set_state(StaffQuickSaleStates.waiting_for_customer_code)
    await callback.message.answer(
        _(
            "Введите новый код клиента для быстрой продажи.\n"
            "Если клиент без кода — нажмите «Без кода (Аноним)» или отправьте «-».",
        ),
        reply_markup=build_staff_sale_code_prompt_keyboard(),
    )
    await callback.answer(_("Можно ввести другой код"))


@router.callback_query(
    StaffQuickSaleStates.browsing_menu,
    lambda c: c.data == "staffsale:comment",
)
async def staff_sale_comment_prompt_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.set_state(StaffQuickSaleStates.waiting_for_comment)
    await callback.message.answer(
        _(
            "Отправьте комментарий к продаже одним сообщением. Чтобы очистить комментарий, "
            "отправьте символ `-`.",
        ),
    )
    await callback.answer()


@router.callback_query(
    StaffQuickSaleStates.browsing_menu,
    lambda c: c.data == "staffsale:comment_clear",
)
async def staff_sale_comment_clear_handler(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    try:
        preview_data = _require_staff_sale_preview_state(data)
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_quick_sale_allowed(employee)
        selected_category_id, preview_chat_id, preview_message_id = await _render_staff_sale_message(
            bot=bot,
            chat_id=preview_data["preview_chat_id"],
            message_id=preview_data["preview_message_id"],
            partner_id=partner.id,
            customer_code=preview_data["customer_code"],
            items_map=preview_data["items"],
            selected_category_id=preview_data["selected_category_id"],
            comment="",
            redeem_bonus=preview_data["redeem_bonus"],
        )
    except (OrderFlowError, BonusServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        comment="",
        selected_category_id=selected_category_id,
        preview_chat_id=preview_chat_id,
        preview_message_id=preview_message_id,
    )
    await callback.answer(_("Комментарий убран"))


@router.callback_query(StaffQuickSaleStates.browsing_menu, lambda c: c.data == "staffsale:cancel")
async def staff_sale_cancel_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit_message_text(callback.message, _("Черновик быстрой продажи отменён."))
    await callback.answer(_("Отменено"))


@router.callback_query(StaffQuickSaleStates.browsing_menu, lambda c: c.data == "staffsale:clear")
async def staff_sale_clear_handler(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    try:
        preview_data = _require_staff_sale_preview_state(data)
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_quick_sale_allowed(employee)
        selected_category_id, preview_chat_id, preview_message_id = await _render_staff_sale_message(
            bot=bot,
            chat_id=preview_data["preview_chat_id"],
            message_id=preview_data["preview_message_id"],
            partner_id=partner.id,
            customer_code=preview_data["customer_code"],
            items_map={},
            selected_category_id=preview_data["selected_category_id"],
            comment=preview_data["comment"],
            redeem_bonus=preview_data["redeem_bonus"],
        )
    except (OrderFlowError, BonusServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        items={},
        selected_category_id=selected_category_id,
        preview_chat_id=preview_chat_id,
        preview_message_id=preview_message_id,
    )
    await callback.answer(_("Позиции очищены"))


@router.callback_query(
    StaffQuickSaleStates.browsing_menu,
    lambda c: c.data and c.data.startswith("staffsale:category:"),
)
async def staff_sale_category_handler(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    _unused, _unused, category_id = (callback.data or "").split(":", 2)
    try:
        preview_data = _require_staff_sale_preview_state(data)
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_quick_sale_allowed(employee)
        selected_category_id, preview_chat_id, preview_message_id = await _render_staff_sale_message(
            bot=bot,
            chat_id=preview_data["preview_chat_id"],
            message_id=preview_data["preview_message_id"],
            partner_id=partner.id,
            customer_code=preview_data["customer_code"],
            items_map=preview_data["items"],
            selected_category_id=category_id,
            comment=preview_data["comment"],
            redeem_bonus=preview_data["redeem_bonus"],
        )
    except (OrderFlowError, BonusServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        selected_category_id=selected_category_id,
        preview_chat_id=preview_chat_id,
        preview_message_id=preview_message_id,
    )
    await callback.answer()


@router.callback_query(
    StaffQuickSaleStates.browsing_menu,
    lambda c: c.data and c.data.startswith("staffsale:add:"),
)
@router.callback_query(
    StaffQuickSaleStates.browsing_menu,
    lambda c: c.data and c.data.startswith("staffsale:sub:"),
)
async def staff_sale_item_quantity_handler(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    _unused, action, item_id = (callback.data or "").split(":", 2)
    try:
        preview_data = _require_staff_sale_preview_state(data)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    items_map = dict(preview_data["items"])
    current_quantity = int(items_map.get(item_id, 0))
    if action == "add":
        items_map[item_id] = current_quantity + 1
    else:
        new_quantity = current_quantity - 1
        if new_quantity > 0:
            items_map[item_id] = new_quantity
        else:
            items_map.pop(item_id, None)

    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_quick_sale_allowed(employee)
        selected_category_id, preview_chat_id, preview_message_id = await _render_staff_sale_message(
            bot=bot,
            chat_id=preview_data["preview_chat_id"],
            message_id=preview_data["preview_message_id"],
            partner_id=partner.id,
            customer_code=preview_data["customer_code"],
            items_map=items_map,
            selected_category_id=preview_data["selected_category_id"],
            comment=preview_data["comment"],
            redeem_bonus=preview_data["redeem_bonus"],
        )
    except (OrderFlowError, BonusServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        items=items_map,
        selected_category_id=selected_category_id,
        preview_chat_id=preview_chat_id,
        preview_message_id=preview_message_id,
    )
    await callback.answer(_("Черновик обновлён"))


@router.message(StaffQuickSaleStates.waiting_for_comment)
async def staff_sale_comment_message_handler(
    message: Message,
    bot: Bot,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    raw_comment = (message.text or "").strip()
    comment = "" if raw_comment == "-" else raw_comment
    employee = None
    try:
        preview_data = _require_staff_sale_preview_state(data)
        partner, employee = await _resolve_staff_employee(message, bot)
        _ensure_quick_sale_allowed(employee)
        selected_category_id, preview_chat_id, preview_message_id = await _render_staff_sale_message(
            bot=bot,
            chat_id=preview_data["preview_chat_id"],
            message_id=preview_data["preview_message_id"],
            partner_id=partner.id,
            customer_code=preview_data["customer_code"],
            items_map=preview_data["items"],
            selected_category_id=preview_data["selected_category_id"],
            comment=comment,
            redeem_bonus=preview_data["redeem_bonus"],
        )
    except (OrderFlowError, BonusServiceError) as exc:
        await message.answer(
            str(exc),
            reply_markup=_build_staff_home_markup(employee),
        )
        return

    await state.set_state(StaffQuickSaleStates.browsing_menu)
    await state.update_data(
        comment=comment,
        selected_category_id=selected_category_id,
        preview_chat_id=preview_chat_id,
        preview_message_id=preview_message_id,
    )
    await message.answer(_("Комментарий сохранён.") if comment else _("Комментарий очищен."))


@router.callback_query(StaffQuickSaleStates.browsing_menu, lambda c: c.data == "staffsale:confirm")
async def staff_sale_confirm_handler(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    try:
        preview_data = _require_staff_sale_preview_state(data)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    items_payload = _build_quick_sale_items_payload(preview_data["items"])
    if not items_payload:
        await callback.answer(_("Сначала добавьте позиции."), show_alert=True)
        return

    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        _ensure_quick_sale_allowed(employee)
        items_count = len(items_payload)
        sale = await sync_to_async(register_walk_in_sale_from_menu_items_by_customer_code)(
            partner_id=partner.id,
            customer_code=preview_data["customer_code"],
            items=items_payload,
            comment=preview_data["comment"],
            created_by=employee.user,
            redeem_bonus=preview_data["redeem_bonus"],
        )
    except (OrderFlowError, BonusServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.clear()
    result_lines = [
        _("<b>Быстрая продажа оформлена</b>"),
        _("Клиент: {loyalty}", loyalty=sale.loyalty_label),
        _("Сумма: {amount} грн", amount=sale.amount),
    ]
    if sale.bonus_spent_amount > 0:
        result_lines.append(_(
            "Списано бонусами: {bonus_spent_amount}",
            bonus_spent_amount=sale.bonus_spent_amount,
        ))
        result_lines.append(_("К оплате: {net_amount} грн", net_amount=sale.net_amount))
    result_lines.extend(
        [
            _(
                "Начислено бонусов: +{bonus_awarded_amount}",
                bonus_awarded_amount=sale.bonus_awarded_amount,
            ),
            _("Позиций: {items_count}", items_count=items_count),
            _("Комментарий: {comment}", comment=escape_html(sale.comment) or _('без комментария')),
        ]
    )
    await _safe_edit_message_text(callback.message, "\n".join(result_lines))
    await callback.answer(_("Продажа сохранена"))


# --- Bill changes (B3.2 part A, PQ-028) -------------------------------------------

_ADJUSTMENT_REASON_CODES = {
    "e": BillAdjustment.ReasonCode.INPUT_ERROR,
    "g": BillAdjustment.ReasonCode.GUEST_REFUSED,
    "c": BillAdjustment.ReasonCode.COMPLIMENT,
    "o": BillAdjustment.ReasonCode.OTHER,
}


async def _resolve_billing_staff(callback: CallbackQuery, bot: Bot):
    partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
    if not _can_manage_billing(employee):
        raise OrderFlowError(_(
            "Работа со счетами доступна только владельцу, менеджеру или кассиру.",
        ))
    return partner, employee


def _active_bill_line(partner_id, order_item_id) -> BillItem:
    return BillItem.objects.select_related("bill").get(
        partner_id=partner_id,
        order_item_id=order_item_id,
        is_active=True,
    )


async def _show_adjustment_result(
    callback: CallbackQuery,
    *,
    partner_id,
    actor_user,
    adjustment: BillAdjustment,
    notice: str,
) -> None:
    bill = await sync_to_async(BillRepository.all_for_partner(partner_id).get)(
        id=adjustment.bill_id,
    )
    bill_text, _markup = await sync_to_async(_build_bill_card_message)(bill, actor_user)
    await _safe_edit_message_text(
        callback.message,
        f"{bill_text}\n\n<i>{escape_html(notice)}</i>",
        reply_markup=build_staff_adjustment_done_keyboard(
            bill_public_id=bill.public_id,
            adjustment_id=str(adjustment.id),
        ),
    )
    await callback.answer(notice)


@router.callback_query(lambda c: c.data and c.data.startswith("staffbilledit:"))
async def staff_bill_edit_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_billing_staff(callback, bot)
        _unused, bill_public_id = (callback.data or "").split(":", 1)
        bill = await sync_to_async(BillRepository.by_public_id_for_partner)(
            partner.id,
            bill_public_id,
        )
        text, markup = await sync_to_async(_build_bill_edit_view)(bill, employee.user)
    except Bill.DoesNotExist:
        await callback.answer(_("Счёт не найден."), show_alert=True)
        return
    except (OrderFlowError, BillingServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _safe_edit_message_text(callback.message, text, reply_markup=markup)
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("staffbillrm:"))
async def staff_bill_remove_line_prompt_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, _employee = await _resolve_billing_staff(callback, bot)
        _unused, order_item_id = (callback.data or "").split(":", 1)
        line = await sync_to_async(_active_bill_line)(partner.id, order_item_id)
    except (BillItem.DoesNotExist, ValueError):
        await callback.answer(_("Позиция уже убрана из счёта."), show_alert=True)
        return
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _safe_edit_message_text(
        callback.message,
        (
            _(
                "<b>Счёт #{public_id}</b>\n"
                "Убрать «{item_name} x{quantity}» ({line_total} грн)?\n"
                "Выберите причину.",
                public_id=line.bill.public_id,
                item_name=escape_html(line.item_name),
                quantity=line.quantity,
                line_total=line.line_total,
            )
        ),
        reply_markup=build_staff_adjustment_reason_keyboard(
            callback_prefix=f"staffbillrr:{order_item_id}",
            bill_public_id=line.bill.public_id,
        ),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("staffbillrr:"))
async def staff_bill_remove_line_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_billing_staff(callback, bot)
        _unused, order_item_id, code = (callback.data or "").split(":", 2)
        line = await sync_to_async(_active_bill_line)(partner.id, order_item_id)
        adjustment = await sync_to_async(remove_bill_lines)(
            bill=line.bill,
            order_item_ids=[order_item_id],
            actor=employee.user,
            reason_code=_ADJUSTMENT_REASON_CODES.get(code, ""),
        )
    except (BillItem.DoesNotExist, ValueError):
        await callback.answer(_("Позиция уже убрана из счёта."), show_alert=True)
        return
    except (OrderFlowError, BillingServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _show_adjustment_result(
        callback,
        partner_id=partner.id,
        actor_user=employee.user,
        adjustment=adjustment,
        notice=_("Позиция убрана из счёта"),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("staffbilloc:"))
async def staff_bill_cancel_order_prompt_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, _employee = await _resolve_billing_staff(callback, bot)
        _unused, order_public_id = (callback.data or "").split(":", 1)
        line = await sync_to_async(
            lambda: BillItem.objects.select_related("bill")
            .filter(partner_id=partner.id, order__public_id=order_public_id, is_active=True)
            .first()
        )()
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if line is None:
        await callback.answer(_("Заказа уже нет в открытом счёте."), show_alert=True)
        return
    await _safe_edit_message_text(
        callback.message,
        (
            _(
                "<b>Счёт #{public_id}</b>\n"
                "Отменить заказ #{order_public_id} и убрать его позиции из счёта?\n"
                "Выберите причину.",
                public_id=line.bill.public_id,
                order_public_id=escape_html(order_public_id),
            )
        ),
        reply_markup=build_staff_adjustment_reason_keyboard(
            callback_prefix=f"staffbillor:{order_public_id}",
            bill_public_id=line.bill.public_id,
        ),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("staffbillor:"))
async def staff_bill_cancel_order_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_billing_staff(callback, bot)
        _unused, order_public_id, code = (callback.data or "").split(":", 2)
        order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id,
            order_public_id,
        )
        await sync_to_async(cancel_order)(
            order=order,
            actor=employee.user,
            reason_code=_ADJUSTMENT_REASON_CODES.get(code, ""),
        )
        adjustment = await sync_to_async(
            lambda: BillAdjustment.objects.filter(
                partner_id=partner.id,
                actor=employee.user,
                kind=BillAdjustment.Kind.ORDER_CANCELED,
            )
            .order_by("-created_at")
            .first()
        )()
    except (Order.DoesNotExist, ValueError):
        await callback.answer(_("Заказ не найден."), show_alert=True)
        return
    except (OrderFlowError, BillingServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _show_adjustment_result(
        callback,
        partner_id=partner.id,
        actor_user=employee.user,
        adjustment=adjustment,
        notice=_("Заказ #{order_public_id} отменён", order_public_id=order_public_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("staffbillvoid:"))
async def staff_bill_void_prompt_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_billing_staff(callback, bot)
        _unused, bill_public_id = (callback.data or "").split(":", 1)
        bill = await sync_to_async(BillRepository.by_public_id_for_partner)(
            partner.id,
            bill_public_id,
        )
        _can_edit, can_void = await sync_to_async(_bill_change_flags)(bill, employee.user)
        if not can_void:
            raise BillingServiceError(
                _("Этот счёт аннулировать нельзя: нет права, он закрыт или по нему есть оплата.")
            )
    except Bill.DoesNotExist:
        await callback.answer(_("Счёт не найден."), show_alert=True)
        return
    except (OrderFlowError, BillingServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    bonus_hint = (
        _(
            "\n"
            "Списанные бонусы ({bonus_spent_amount}) вернутся гостю.",
            bonus_spent_amount=bill.bonus_spent_amount,
        )
        if bill.bonus_spent_amount > 0
        else ""
    )
    await _safe_edit_message_text(
        callback.message,
        (
            _(
                "<b>Аннулировать счёт #{public_id}?</b>\n"
                "Сумма: {total_amount} грн. Заказы снова можно будет выставить в "
                "счёт.{bonus_hint}\n"
                "Выберите причину.",
                public_id=bill.public_id,
                total_amount=bill.total_amount,
                bonus_hint=bonus_hint,
            )
        ),
        reply_markup=build_staff_adjustment_reason_keyboard(
            callback_prefix=f"staffbillvr:{bill.public_id}",
            bill_public_id=bill.public_id,
        ),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("staffbillvr:"))
async def staff_bill_void_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_billing_staff(callback, bot)
        _unused, bill_public_id, code = (callback.data or "").split(":", 2)
        bill = await sync_to_async(BillRepository.by_public_id_for_partner)(
            partner.id,
            bill_public_id,
        )
        adjustment = await sync_to_async(void_bill)(
            bill=bill,
            actor=employee.user,
            reason_code=_ADJUSTMENT_REASON_CODES.get(code, ""),
        )
    except (Bill.DoesNotExist, ValueError):
        await callback.answer(_("Счёт не найден."), show_alert=True)
        return
    except (OrderFlowError, BillingServiceError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _show_adjustment_result(
        callback,
        partner_id=partner.id,
        actor_user=employee.user,
        adjustment=adjustment,
        notice=_("Счёт #{bill_public_id} аннулирован", bill_public_id=bill_public_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("staffadjc:"))
async def staff_adjustment_comment_prompt_handler(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    try:
        partner, employee = await _resolve_billing_staff(callback, bot)
        _unused, adjustment_id = (callback.data or "").split(":", 1)
        is_author = await sync_to_async(
            BillAdjustment.objects.filter(
                id=adjustment_id,
                partner_id=partner.id,
                actor=employee.user,
                reason_text="",
            ).exists
        )()
    except ValueError:
        await callback.answer(_("Изменение не найдено."), show_alert=True)
        return
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not is_author:
        await callback.answer(_("Комментарий уже добавлен или изменение не ваше."), show_alert=True)
        return
    await state.set_state(StaffBillStates.waiting_for_adjustment_comment)
    await state.update_data(adjustment_id=adjustment_id)
    await callback.message.answer(_("Напишите комментарий к изменению счёта одним сообщением."))
    await callback.answer()


@router.message(StaffBillStates.waiting_for_adjustment_comment)
async def staff_adjustment_comment_message_handler(
    message: Message,
    bot: Bot,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    await state.clear()
    try:
        partner, employee = await _resolve_staff_employee(message, bot)
        await sync_to_async(annotate_bill_adjustment)(
            adjustment_id=data.get("adjustment_id"),
            partner_id=partner.id,
            actor=employee.user,
            text=message.text or "",
        )
    except (OrderFlowError, BillingServiceError) as exc:
        await message.answer(escape_html(str(exc)))
        return
    await message.answer(_("Комментарий сохранён в истории счёта."))
