"""Telegram Mini App for staff (PQ-048, stage 2): open orders and guest calls.

The same actions as the bot's work mode (PQ-044), through the same services:
statuses (``transition_order_status``, ``cancel_order``), the transfer check
(``confirm_transfer`` / ``reject_transfer``), payment on receipt
(``record_payment_on_receipt`` / ``cancel_unpaid_remote_order``), the ТТН
(``mark_order_shipped``), «Иду» on a call (``take_guest_call``). Which actions
an order offers is ``apps/billing/staff_actions.py:staff_order_actions`` —
the same list as the bot's buttons; it is checked again before acting, so a
screen that is out of date cannot do what the order no longer allows.
Bills, tables, quick sales and reports stay in the bot for now.
"""

from __future__ import annotations

from datetime import timedelta
from functools import wraps

from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt

from apps.billing import staff_actions as actions
from apps.billing.models import Payment
from apps.billing.remote_orders import (
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
from apps.billing.services import BillingServiceError, cancel_order
from apps.billing.staff_actions import staff_order_actions
from apps.bonuses.services import BonusServiceError
from apps.employees.invites import roles_invitable_by
from apps.notifications.models import StaffNotification
from apps.notifications.services import GuestCallError, take_guest_call
from apps.orders.models import Order
from apps.orders.repositories import OrderRepository
from apps.orders.selectors import get_open_orders_for_partner
from apps.orders.services import (
    OrderFlowError,
    is_order_paid,
    is_order_received,
    requested_time_label,
    transition_order_status,
)
from apps.tables.services import TableSessionError
from bot.services.staff_rights import (
    can_manage_billing,
    can_quick_sale,
    can_view_day_report,
    can_view_tables,
)
from core.i18n import N_, _, texts
from webapp.context import WebAppDenied, find_bot
from webapp.staff_context import StaffRequest, staff_request
from webapp.views import _body, _denied, _error, _money, _text

# The bot's catalog texts (``bot/keyboards/staff.py``), same entries.
STATUS_LABELS = {
    "accepted": N_("Взять в работу"),
    "preparing": N_("Готовим"),
    "ready": N_("Готово"),
    "delivering": N_("Нести"),
    "completed": N_("Завершить"),
    "canceled": N_("Отменить"),
}
DELIVERY_STATUS_LABELS = {
    **STATUS_LABELS,
    "delivering": N_("🚚 В пути"),
    "completed": N_("✅ Доставлено"),
}
SHIPPING_STATUS_LABELS = {
    **STATUS_LABELS,
    "ready": N_("📦 Собран"),
    "delivering": N_("🚚 Отправлен"),
    "completed": N_("✅ Получен"),
}
STAFF_TEXTS = {
    "title": N_("Рабочий режим"),
    "orders": N_("Открытые заказы"),
    "calls": N_("Вызовы"),
    "no_orders": N_("Сейчас открытых заказов нет."),
    "no_calls": N_("Вызовов нет."),
    "all": N_("Все"),
    "new": N_("Новый"),
    "go": N_("🏃 Иду"),
    "taken": N_("Уже идёт: {name}"),
    "table": N_("Стол {number}"),
    "phone": N_("Телефон"),
    "address": N_("Адрес"),
    "map": N_("Открыть на карте"),
    "recipient": N_("Получатель"),
    "shipping_cost": N_("Пересылка"),
    "tracking": N_("ТТН"),
    "tracking_prompt": N_("Номер ТТН"),
    "comment": N_("Комментарий"),
    "assignee": N_("Взял в работу"),
    "received": N_("Гость подтвердил получение"),
    "total": N_("Сумма"),
    "payment": N_("Оплата"),
    "minutes": N_("{minutes} мин"),
    "confirm": N_("Подтвердить"),
    "back": N_("Назад"),
    "change_point": N_("Сменить заведение"),
    "updated": N_("Готово"),
    "stale": N_("Заказ уже обновлён, показываю актуальный статус"),
    "error": N_("Что-то пошло не так. Попробуйте ещё раз."),
    "retry": N_("Повторить"),
    "currency": N_("грн"),
    "sound_on": N_("🔔 Звук новых заказов"),
    "bills": N_("Счета"),
    "bonuses": N_("Бонусы"),
    "tables": N_("Столы"),
    "no_bills": N_("Открытых счетов и запросов нет."),
    "no_tables": N_("Сейчас за столами никого нет."),
    "bill_requests": N_("Просят счёт"),
    "open_bills": N_("Открытые счета"),
    "unbilled": N_("Заказов без счёта: {count}"),
    "paid": N_("Оплачено"),
    "remaining": N_("К оплате"),
    "bonus_offer": N_("У гостя {balance} бонусов — можно списать до {amount} грн."),
    "removed": N_("Убрано позиций: {count} (см. историю счёта)"),
    "process": N_("Обработан"),
    "cancel_request": N_("Отменить запрос"),
    "open_bill": N_("Открыть счёт"),
    "visit": N_("Визит с {since}"),
    "guests": N_("Гостей: {count}"),
    "not_billed": N_("Без счёта: {count} на {total} грн"),
    "shared_bill": N_("Общий счёт"),
    "personal_bills": N_("Счёт каждому"),
    "close_visit": N_("Закрыть визит"),
    "close_visit_confirm": N_("Закрыть визит? Стол освободится, гости больше не смогут заказать."),
    "pick_reason": N_("Выберите причину."),
    "refund": N_("↩️ Возврат"),
    "refunded": N_("уже возвращено"),
    "refund_amounts": N_("Гостю вернётся {money} грн и {bonus} бонусов."),
    "refund_reason": N_("Причина возврата?"),
    "refund_method": N_("Как вернёте деньги гостю?"),
    "pick_lines": N_("Отметьте, что вернуть."),
    "next": N_("Дальше →"),
    "find_order": N_("Найти заказ по номеру"),
    "order_number": N_("Номер заказа"),
    "change_bill": N_("Убрать позицию (останется в истории счёта) или отменить заказ целиком:"),
    "remove_line_q": N_("Убрать «{name}» из счёта?"),
    "cancel_order": N_("Отменить заказ #{id}"),
    "void_bill": N_("Аннулировать"),
    "sale": N_("Продажа"),
    "report": N_("Отчёт дня"),
    "customer_code": N_("Код клиента"),
    "customer_code_hint": N_("Код клиента — для бонусов; без кода — анонимная продажа"),
    "sale_redeem": N_("Списать бонусы: до {amount} грн"),
    "sale_bonus": N_("Гостю начислится бонусов: {bonus}"),
    "sale_confirm": N_("Провести продажу"),
    "clear": N_("Очистить"),
    "to_pay": N_("К оплате"),
    "by_channel": N_("По способам заказа"),
    "by_method": N_("Методы оплаты"),
    "by_staff": N_("По сотрудникам"),
    "invite": N_("Пригласить сотрудника"),
    "invite_role": N_("С какой ролью? Ссылка будет одноразовой и проработает 24 часа."),
    "invite_ready": N_(
        "Отправьте эту ссылку сотруднику. Когда он нажмёт «Start», бот пришлёт вам "
        "кнопки «Подтвердить» и «Отклонить».",
    ),
    "share": N_("📤 Переслать ссылку"),
    "copy": N_("Скопировать"),
    "copied": N_("Ссылка скопирована"),
}


def _labels_for(order: Order) -> dict[str, str]:
    if order.channel == Order.Channel.DELIVERY:
        return DELIVERY_STATUS_LABELS
    if order.channel == Order.Channel.SHIPPING:
        return SHIPPING_STATUS_LABELS
    return STATUS_LABELS


def _action_json(order: Order, code: str) -> dict:
    is_shipping = order.channel == Order.Channel.SHIPPING
    if code.startswith(actions.STATUS):
        status = code[len(actions.STATUS):]
        data = {"code": code, "label": _(_labels_for(order).get(status, status))}
        if status == Order.Status.CANCELED:
            data.update(style="danger", confirm=_("Отменить заказ?"))
        return data
    if code == actions.TRANSFER_OK:
        return {"code": code, "label": _("✅ Оплата получена"), "style": "primary"}
    if code == actions.TRANSFER_NO:
        return {
            "code": code, "label": _("❌ Оплаты нет"), "style": "danger",
            "confirm": _(
                "Отменить заказ, потому что оплата не поступила? Списанные бонусы вернутся "
                "гостю, гость получит сообщение."
            ),
        }
    if code == actions.RECEIPT_CASH:
        return {"code": code, "label": _("💵 Оплачено наличными"), "style": "primary"}
    if code == actions.RECEIPT_CARD:
        return {"code": code, "label": _("💳 Оплачено картой"), "style": "primary"}
    if code == actions.RECEIPT_COD:
        return {"code": code, "label": _("💵 Наложенный платёж получен"), "style": "primary"}
    if code == actions.RECEIPT_NO:
        courier_has_it = (
            order.channel != Order.Channel.DELIVERY or order.status == Order.Status.DELIVERING
        )
        label = (
            _("❌ Посылку не забрали") if is_shipping
            else _("❌ Гость не забрал") if courier_has_it
            else _(STATUS_LABELS["canceled"])
        )
        return {
            "code": code, "label": label, "style": "danger",
            "confirm": _("Отменить заказ? Списанные бонусы вернутся гостю."),
        }
    if code == actions.SHIP:
        return {"code": code, "label": _(SHIPPING_STATUS_LABELS["delivering"]), "input": "tracking"}
    return {"code": code, "label": code}


def _payment_label(order: Order) -> str:
    """As the bot's order card (``bot/handlers/staff.py:_order_payment_label``)."""

    bonus_note = _(" (ещё {bonus_spent} бонусами)", bonus_spent=order.bonus_spent) \
        if order.bonus_spent else ""
    if awaits_transfer_check(order):
        return _(
            "ждёт подтверждения перевода {to_transfer} грн{bonus_note}",
            to_transfer=order.total_amount - order.bonus_spent, bonus_note=bonus_note,
        )
    if awaits_payment_on_receipt(order):
        return _(
            "при получении, {to_pay} грн{bonus_note}",
            to_pay=order.total_amount - order.bonus_spent, bonus_note=bonus_note,
        )
    return _("Оплачен") if is_order_paid(order) else _("Не оплачен")


def _place(order: Order) -> str:
    if order.channel == Order.Channel.PICKUP:
        return _("Самовывоз")
    if order.channel == Order.Channel.DELIVERY:
        return _("Доставка")
    if order.channel == Order.Channel.SHIPPING:
        return _("Почта")
    if order.table_id is not None:
        return _("Стол {number}", number=order.table.number)
    return _("Вне стола")


def _order_json(order: Order, *, now) -> dict:
    remote = order.channel in Order.REMOTE_CHANNELS
    assignee = order.assigned_employee
    data = {
        "id": order.public_id,
        "status": order.status,
        "status_label": order.get_status_display(),
        "channel": order.channel,
        "place": _place(order),
        "when": requested_time_label(order) if order.channel in (
            Order.Channel.PICKUP, Order.Channel.DELIVERY
        ) else "",
        "minutes": max(0, int((now - order.created_at).total_seconds() // 60)),
        "total": _money(order.total_amount),
        "payment": _payment_label(order),
        "paid": is_order_paid(order),
        "guest": order.guest.staff_label(),
        "phone": order.guest.phone if remote else "",
        "items": [
            {"name": item.item_name, "quantity": item.quantity} for item in order.items.all()
        ],
        "comment": order.comment,
        "assignee": (assignee.user.get_full_name().strip() or assignee.user.username)
        if assignee is not None else "",
        "received": is_order_received(order) and order.status != Order.Status.COMPLETED,
        "actions": [_action_json(order, code) for code in staff_order_actions(order)],
        # Refund of a paid order (PQ-037): the right is checked when it opens.
        "can_refund": order.paid_at is not None and order.status != Order.Status.CANCELED,
    }
    if order.channel == Order.Channel.DELIVERY:
        data["address"] = order.delivery_address
        if order.delivery_latitude is not None:
            data["map"] = map_link(order.delivery_latitude, order.delivery_longitude)
    if order.channel == Order.Channel.SHIPPING:
        data.update(
            carrier=order.get_shipping_carrier_display(),
            recipient=order.shipping_destination,
            shipping_cost=shipping_cost_label(order),
            tracking=order.tracking_number,
            tracking_link=tracking_link(order.shipping_carrier, order.tracking_number)
            if order.tracking_number else "",
        )
    return data


def staff_api(method: str):
    def decorate(view):
        @csrf_exempt  # no cookies: the signed header is the credential
        @wraps(view)
        def wrapper(request, bot_hex: str):
            if request.method != method:
                return _error(405, "method not allowed")
            try:
                body = _body(request)
                staff = staff_request(request, bot_hex)
            except WebAppDenied as exc:
                return _denied(exc)
            with texts(staff.language, staff.overrides):
                if not staff.content.supports_orders():
                    return _error(403, _("Модуль заказов отключён для этого заведения."), "off")
                try:
                    return JsonResponse(view(request, staff, body))
                except WebAppDenied as exc:
                    return _error(exc.status, _(exc.message), exc.code)
                except (
                    OrderFlowError, BillingServiceError, GuestCallError, TableSessionError,
                    BonusServiceError,
                ) as exc:
                    return _error(400, str(exc), "refused")

        return wrapper

    return decorate


@xframe_options_exempt
def staff_page(request, bot_hex: str):
    try:
        find_bot(bot_hex)
    except WebAppDenied as exc:
        raise Http404 from exc
    response = render(request, "webapp/staff.html", {"bot_hex": bot_hex})
    response["Content-Security-Policy"] = (
        "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
    )
    response["Cache-Control"] = "no-store"
    return response


# Calls older than this are not shown: the guest has long been helped.
CALL_HOURS = 3


def _open_calls(staff: StaffRequest):
    since = timezone.now() - timedelta(hours=CALL_HOURS)
    return (
        StaffNotification.objects.filter(
            partner=staff.partner,
            employee=staff.employee,
            category=StaffNotification.Category.GUEST_CALL,
            guest_call__isnull=False,
            guest_call__taken_by__isnull=True,
            created_at__gte=since,
        )
        .select_related("guest_call")
        .order_by("-created_at")[:30]
    )


@staff_api("GET")
def staff_state_view(request, staff: StaffRequest, body: dict) -> dict:
    now = timezone.now()
    orders = list(get_open_orders_for_partner(staff.partner.id))
    calls = [
        {
            "id": notification.id.hex,
            "table": notification.guest_call.table_number,
            "text": notification.message,
            "minutes": max(0, int((now - notification.created_at).total_seconds() // 60)),
        }
        for notification in _open_calls(staff)
    ]
    employee = staff.employee
    return {
        "texts": {key: _(msgid) for key, msgid in STAFF_TEXTS.items()},
        "venue": {"id": staff.partner.id.hex, "name": staff.partner.name},
        "venues": [
            {"id": venue.id.hex, "name": venue.name} for venue in staff.venues
        ] if len(staff.venues) > 1 else [],
        "employee": employee.user.get_full_name().strip() or employee.user.username,
        "rights": {
            "billing": can_manage_billing(employee, staff.content),
            "tables": can_view_tables(employee, staff.content),
            "sale": can_quick_sale(employee, staff.content),
            "report": can_view_day_report(employee, staff.content),
            "invite": bool(roles_invitable_by(employee.user, staff.partner.id)),
        },
        "orders": [_order_json(order, now=now) for order in orders],
        "calls": calls,
    }


def _load_order(staff: StaffRequest, public_id: str) -> Order:
    try:
        return OrderRepository.by_public_id_for_partner(staff.partner.id, public_id)
    except Order.DoesNotExist as exc:
        raise WebAppDenied(404, N_("Заказ не найден.")) from exc


@staff_api("GET")
def staff_order_view(request, staff: StaffRequest, body: dict) -> dict:
    """One order of this venue by its number, open or not (to find and refund it)."""

    public_id = (request.GET.get("id") or "").strip().lower().lstrip("#")
    return {"order": _order_json(_load_order(staff, public_id), now=timezone.now())}


RECEIPT_METHODS = {
    actions.RECEIPT_CASH: Payment.Method.CASH,
    actions.RECEIPT_CARD: Payment.Method.TERMINAL,
    actions.RECEIPT_COD: Payment.Method.COD,
}


@staff_api("POST")
def staff_action_view(request, staff: StaffRequest, body: dict) -> dict:
    """``{"order": …, "action": …, "tracking": …}``: one button of the card."""

    order = _load_order(staff, _text(body, "order"))
    code = _text(body, "action")
    if code not in staff_order_actions(order):
        # The card was out of date: someone else already moved the order.
        return {"ok": False, "stale": True, "order": _order_json(order, now=timezone.now())}
    actor = staff.employee.user
    if code.startswith(actions.STATUS):
        status = code[len(actions.STATUS):]
        if status == Order.Status.CANCELED:
            cancel_order(order=order, actor=actor, note="Cancelled via Mini App")
        else:
            transition_order_status(
                order=order, to_status=status, actor_user=actor, note="Updated via Mini App"
            )
    elif code == actions.TRANSFER_OK:
        confirm_transfer(order=order, actor=actor)
    elif code == actions.TRANSFER_NO:
        reject_transfer(order=order, actor=actor)
    elif code in RECEIPT_METHODS:
        record_payment_on_receipt(order=order, actor=actor, method=RECEIPT_METHODS[code])
    elif code == actions.RECEIPT_NO:
        cancel_unpaid_remote_order(order=order, actor=actor)
    elif code == actions.SHIP:
        mark_order_shipped(order=order, actor=actor, tracking_number=_text(body, "tracking"))
    order = _load_order(staff, order.public_id)
    return {"ok": True, "order": _order_json(order, now=timezone.now())}


@staff_api("POST")
def staff_call_view(request, staff: StaffRequest, body: dict) -> dict:
    """«🏃 Иду» on a guest call: the first one takes it (``take_guest_call``)."""

    import uuid

    try:
        notification_id = uuid.UUID(hex=_text(body, "call"))
    except ValueError as exc:
        raise WebAppDenied(400, N_("Этот вызов уже нельзя принять.")) from exc
    call = take_guest_call(
        partner_id=staff.partner.id, notification_id=notification_id, employee=staff.employee
    )
    return {"ok": True, "table": call.table_number}
