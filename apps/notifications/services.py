from __future__ import annotations

import asyncio
import html
import uuid
from datetime import timedelta

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from asgiref.sync import async_to_sync, sync_to_async
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.employees.models import EmployeeProfile
from apps.notifications.models import BroadcastCampaign, BroadcastDelivery, StaffNotification
from apps.notifications.repositories import (
    BroadcastCampaignRepository,
    NotificationPreferenceRepository,
)
from apps.partners.models import BotInstance, Partner
from apps.tables.services import get_active_table_session
from apps.users.models import TelegramAccount
from core.settings.env import app_settings

DELIVER_STAFF_NOTIFICATION_TASK = "notifications.deliver_staff_notification"
SEND_GUEST_ORDER_STATUS_UPDATE_TASK = "notifications.send_guest_order_status_update"
SEND_BROADCAST_CAMPAIGN_TASK = "notifications.send_broadcast_campaign"
PROCESS_SCHEDULED_BROADCAST_CAMPAIGNS_TASK = "notifications.process_scheduled_broadcast_campaigns"
RECOVER_STALLED_BROADCASTS_TASK = "notifications.recover_stalled_broadcast_campaigns"
# A claimed broadcast message whose result is not recorded within this time is
# treated as lost by its worker: it becomes ``unknown`` and is never resent.
BROADCAST_CLAIM_LEASE = timedelta(minutes=30)
BROADCAST_BATCH_SIZE = max(1, app_settings.notification_broadcast_batch_size)


class NotificationDeliveryError(Exception):
    """Raised when partner bot delivery cannot be performed safely."""


class RetryableDeliveryError(NotificationDeliveryError):
    """A transient Telegram failure: the Celery task retries (PQ-015)."""


class GuestCallError(Exception):
    """Raised when a guest cannot safely request staff assistance."""


def _escape_html(value) -> str:
    """Escape a dynamic value for Telegram HTML parse mode (AA-024)."""

    return html.escape("" if value is None else str(value), quote=False)


def _active_partner_bot(partner_id) -> BotInstance:
    bot_instance = (
        BotInstance.objects.filter(
            partner_id=partner_id,
            is_active=True,
            mode=BotInstance.Mode.POLLING,
        )
        .select_related("partner")
        .order_by("created_at")
        .first()
    )
    if bot_instance is None:
        raise NotificationDeliveryError("No active polling bot is configured for this partner.")
    if bot_instance.partner.status != Partner.Status.ACTIVE:
        raise NotificationDeliveryError("Partner is not active, delivery is disabled.")
    if not bot_instance.has_usable_token:
        raise NotificationDeliveryError("Partner bot token is missing or invalid.")
    return bot_instance


async def _send_telegram_messages(
    token: str,
    messages: list[tuple],
    *,
    on_result=None,
) -> list[tuple[int, bool, str, bool, bool]]:
    """Send messages one by one; never call this inside a database transaction.

    Each result is ``(chat_id, sent, error, blocked, retryable)``. ``retryable``
    marks failures where Telegram may or may not have delivered the message
    (a message is ``(chat_id, text)`` or ``(chat_id, text, reply_markup)``)
    (network or server errors): operational notifications retry them, a
    broadcast records them as ``unknown``. ``on_result`` (async) is awaited
    after every message so callers can persist each outcome immediately.
    """

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    results: list[tuple[int, bool, str, bool, bool]] = []
    try:
        for chat_id, text, *markup in messages:
            while True:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=markup[0] if markup else None,
                    )
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(max(float(exc.retry_after), 1.0))
                    continue
                except TelegramForbiddenError as exc:
                    result = (chat_id, False, str(exc), True, False)
                except (TelegramNetworkError, TelegramServerError) as exc:
                    result = (chat_id, False, str(exc), False, True)
                except TelegramBadRequest as exc:
                    result = (chat_id, False, str(exc), False, False)
                except TelegramAPIError as exc:
                    result = (chat_id, False, str(exc), False, False)
                except Exception as exc:  # noqa: BLE001
                    result = (chat_id, False, str(exc), False, True)
                else:
                    result = (chat_id, True, "", False, False)
                break
            results.append(result)
            if on_result is not None:
                await on_result(result)
    finally:
        await bot.session.close()
    return results


def _unpack_delivery_result(result) -> tuple[int, bool, str, bool]:
    chat_id = result[0]
    is_sent = result[1]
    error_message = result[2]
    should_mark_blocked = result[3] if len(result) > 3 else False
    return chat_id, is_sent, error_message, should_mark_blocked


def _is_retryable_result(result) -> bool:
    return bool(result[4]) if len(result) > 4 else False


@transaction.atomic
def mark_staff_notifications_read(*, employee_id, partner_id) -> int:
    """Mark unread staff notifications as read for one employee within one partner."""

    unread_notifications = StaffNotification.objects.filter(
        employee_id=employee_id,
        partner_id=partner_id,
        is_read=False,
    )
    if not unread_notifications.exists():
        return 0

    now = timezone.now()
    return unread_notifications.update(
        is_read=True,
        read_at=now,
        updated_at=now,
    )


@transaction.atomic
def mark_staff_notification_read(*, notification_id, employee_id, partner_id) -> bool:
    updated = StaffNotification.objects.filter(
        id=notification_id,
        employee_id=employee_id,
        partner_id=partner_id,
        is_read=False,
    ).update(
        is_read=True,
        read_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return bool(updated)


def deliver_staff_notification(notification: StaffNotification):
    from apps.notifications.tasks import deliver_staff_notification_task

    transaction.on_commit(
        lambda: deliver_staff_notification_task.delay(str(notification.id))
    )


def deliver_staff_notification_now(notification: StaffNotification) -> StaffNotification:
    """Deliver one staff notification at least once (PQ-015).

    Already sent notifications are not sent again. The employee must still be
    active, linked to the partner and opted in to bot notifications. Transient
    Telegram failures (network/server errors) are recorded and raised as
    ``RetryableDeliveryError`` so the Celery task retries; definite failures
    are recorded and not retried.
    """

    notification = (
        StaffNotification.objects.select_related(
            "employee__telegram_account",
            "employee__user",
        )
        .filter(id=notification.id)
        .first()
    )
    if notification is None:
        return None
    if notification.delivery_status == StaffNotification.DeliveryStatus.SENT:
        return notification
    employee = notification.employee
    # Security alerts reach the owner even with bot notifications off (AA-035).
    opted_in = (
        employee is not None
        and (
            employee.bot_notifications_enabled
            or notification.category == StaffNotification.Category.SECURITY
        )
    )
    if employee is not None and not (
        employee.is_active
        and opted_in
        and employee.partner_id == notification.partner_id
        and employee.user.is_active
        and employee.user.partner_id == notification.partner_id
    ):
        notification.delivery_status = StaffNotification.DeliveryStatus.FAILED
        notification.delivery_error = "Employee is no longer an active notification recipient."
        notification.save(update_fields=["delivery_status", "delivery_error", "updated_at"])
        return notification
    if employee is None or employee.telegram_account_id is None:
        notification.delivery_status = StaffNotification.DeliveryStatus.FAILED
        notification.delivery_error = "Employee Telegram account is not linked."
        notification.save(update_fields=["delivery_status", "delivery_error", "updated_at"])
        return notification

    try:
        bot_instance = _active_partner_bot(notification.partner_id)
        results = async_to_sync(_send_telegram_messages)(
            bot_instance.token,
            [
                (
                    employee.telegram_account.telegram_id,
                    # Title and message are plain text built from venue and
                    # guest data; only the <b> wrapper is markup (AA-024).
                    f"<b>{_escape_html(notification.title)}</b>\n"
                    f"{_escape_html(notification.message)}",
                )
            ],
        )
    except NotificationDeliveryError as exc:
        notification.delivery_status = StaffNotification.DeliveryStatus.FAILED
        notification.delivery_error = str(exc)
        notification.save(update_fields=["delivery_status", "delivery_error", "updated_at"])
        return notification

    _chat_id, is_sent, error_message, should_mark_blocked = _unpack_delivery_result(results[0])
    notification.delivery_status = (
        StaffNotification.DeliveryStatus.SENT
        if is_sent
        else StaffNotification.DeliveryStatus.FAILED
    )
    notification.delivered_at = timezone.now() if is_sent else None
    notification.delivery_error = error_message
    if should_mark_blocked:
        employee.telegram_account.is_blocked = True
        employee.telegram_account.save(update_fields=["is_blocked", "updated_at"])
    notification.save(
        update_fields=[
            "delivery_status",
            "delivered_at",
            "delivery_error",
            "updated_at",
        ]
    )
    if not is_sent and _is_retryable_result(results[0]):
        raise RetryableDeliveryError(error_message or "Transient Telegram delivery failure.")
    return notification


def _guest_call_role_targets(call_target: str) -> tuple[str, ...]:
    if call_target == "waiter":
        return ("waiter", "manager", "owner")
    if call_target == "bartender":
        return ("cashier", "manager", "owner")
    if call_target == "hookah":
        return ("hookah_master", "manager", "owner")
    raise GuestCallError("Неизвестный тип вызова персонала.")


def _employee_display_name(employee) -> str:
    if employee is None:
        return ""
    full_name = employee.user.get_full_name().strip()
    return full_name or employee.user.username


def _order_delivery_context_label(order) -> str:
    if order.channel == order.Channel.PICKUP:
        from apps.orders.services import requested_time_label

        return f"Самовывоз: {requested_time_label(order)}"
    if order.channel == order.Channel.DELIVERY:
        from apps.orders.services import requested_time_label

        return (
            f"Доставка: {requested_time_label(order)}, "
            f"{_escape_html(order.delivery_address)}"
        )
    if order.channel == order.Channel.SHIPPING:
        return (
            f"Отправка: {_escape_html(order.get_shipping_carrier_display())}, "
            f"{_escape_html(order.shipping_destination)}"
        )
    if order.table_id is not None:
        return f"Стол: #{order.table.number}"
    return "Сценарий: заказ без привязки к столу"


def _remote_status_note(order, to_status: str) -> str:
    if order.channel not in order.REMOTE_CHANNELS:
        return ""
    prepaid = order.payment_timing == order.PaymentTiming.PREPAID
    if to_status == order.Status.ACCEPTED:
        return "Оплата подтверждена — готовим ваш заказ." if prepaid else "Заказ принят — готовим."
    if to_status == order.Status.READY and order.channel == order.Channel.PICKUP:
        return "Заказ готов — можно забирать."
    if order.channel == order.Channel.SHIPPING:
        if to_status == order.Status.READY:
            return "Заказ собран — скоро отправим."
        if to_status == order.Status.DELIVERING:
            from apps.billing.remote_orders import tracking_link

            number = _escape_html(order.tracking_number)
            text = (
                f"Заказ отправлен: {_escape_html(order.get_shipping_carrier_display())}, "
                f"ТТН <code>{number}</code>."
            )
            link = tracking_link(order.shipping_carrier, order.tracking_number)
            if link:
                text += f'\n<a href="{_escape_html(link)}">Отследить посылку</a>'
            return text + "\nКогда заберёте посылку, нажмите «Получил(а)»."
        if to_status == order.Status.COMPLETED:
            return "Заказ получен. Спасибо!"
    if order.channel == order.Channel.DELIVERY:
        if to_status == order.Status.READY:
            return "Заказ собран — скоро передадим курьеру."
        if to_status == order.Status.DELIVERING:
            return "Заказ в пути — курьер уже едет к вам."
        if to_status == order.Status.COMPLETED:
            return "Заказ доставлен. Приятного аппетита!"
    if to_status == order.Status.CANCELED and prepaid and order.paid_at is None:
        return (
            "Оплата не поступила, заказ отменён. Если вы переводили деньги, "
            "свяжитесь с заведением."
        )
    return ""


def send_guest_order_status_update(*, order, from_status: str, to_status: str):
    if not NotificationPreferenceRepository.guest_allows_order_updates(order.guest_id):
        return None

    from apps.notifications.tasks import send_guest_order_status_update_task

    transaction.on_commit(
        lambda: send_guest_order_status_update_task.delay(
            str(order.id),
            from_status,
            to_status,
        )
    )


def send_guest_order_status_update_now(
    *,
    order_id: str,
    from_status: str,
    to_status: str,
) -> bool:
    from apps.orders.models import Order

    try:
        order = (
            Order.objects.select_related(
                "partner",
                "guest__telegram_account",
                "table",
                "assigned_employee__user",
            )
            .get(id=order_id)
        )
    except Order.DoesNotExist:
        return False

    if not NotificationPreferenceRepository.guest_allows_order_updates(order.guest_id):
        return False

    try:
        bot_instance = _active_partner_bot(order.partner_id)
        guest_chat_id = order.guest.telegram_account.telegram_id
    except NotificationDeliveryError:
        return False

    lines = [
        f"<b>Обновление по заказу #{order.public_id}</b>",
        _order_delivery_context_label(order),
        f"Новый статус: {order.get_status_display()}",
        f"Оплата: {'получена' if order.paid_at else 'ещё не зафиксирована'}",
        f"Получение: {'подтверждено' if order.received_at else 'ещё не подтверждено'}",
    ]
    if to_status == order.Status.ACCEPTED and order.assigned_employee_id:
        lines.append(
            f"Заказ взял в работу: {_escape_html(_employee_display_name(order.assigned_employee))}"
        )
    elif order.assigned_employee_id:
        lines.append(
            f"Ответственный: {_escape_html(_employee_display_name(order.assigned_employee))}"
        )

    if order.comment:
        lines.append(f"Комментарий: {_escape_html(order.comment)}")
    remote_note = _remote_status_note(order, to_status)
    if remote_note:
        lines.append(remote_note)

    message = (guest_chat_id, "\n".join(lines))
    if (
        order.channel == order.Channel.SHIPPING
        and to_status == order.Status.DELIVERING
        and order.status == order.Status.DELIVERING
    ):
        # The guest confirms the parcel is received (PQ-029, PQ-034).
        message += (
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Получил(а)",
                            callback_data=f"guestorderconfirm:{order.public_id}",
                        )
                    ]
                ]
            ),
        )
    results = async_to_sync(_send_telegram_messages)(bot_instance.token, [message])
    _chat_id, is_sent, error_message, should_mark_blocked = _unpack_delivery_result(results[0])
    if should_mark_blocked:
        order.guest.telegram_account.is_blocked = True
        order.guest.telegram_account.save(update_fields=["is_blocked", "updated_at"])
    if not is_sent and _is_retryable_result(results[0]):
        # At least once (PQ-015): only a transient failure is retried; nothing
        # after a successful send raises, so a sent update is not repeated.
        raise RetryableDeliveryError(error_message or "Transient Telegram delivery failure.")
    return is_sent


@transaction.atomic
def request_staff_assistance(
    *,
    partner_id,
    telegram_id: int,
    call_target: str,
    call_target_label: str,
    cooldown_seconds: int = 45,
) -> tuple[int, int]:
    # Validate the delivery channel up front so the guest never sees a
    # successful response when the venue bot is not actually able to notify staff.
    try:
        _active_partner_bot(partner_id)
    except NotificationDeliveryError as exc:
        raise GuestCallError(str(exc)) from exc

    table_session = get_active_table_session(partner_id=partner_id, telegram_id=telegram_id)
    if table_session is None:
        raise GuestCallError(
            "Позвать персонал можно только после сканирования QR-кода стола."
        )

    table_number = table_session.table.number
    recent_cutoff = timezone.now() - timedelta(seconds=cooldown_seconds)
    recent_duplicate_exists = StaffNotification.objects.filter(
        partner_id=partner_id,
        category=StaffNotification.Category.GUEST_CALL,
        created_at__gte=recent_cutoff,
        title=f"Вызов: {call_target_label}",
        message__contains=f"Стол: #{table_number}",
    ).exists()
    if recent_duplicate_exists:
        raise GuestCallError(
            "Похожий вызов уже был отправлен совсем недавно. Подождите немного."
        )

    recipient_roles = _guest_call_role_targets(call_target)
    recipients = list(
        EmployeeProfile.objects.select_related("telegram_account", "user", "partner").filter(
            partner_id=partner_id,
            is_active=True,
            bot_notifications_enabled=True,
            notify_on_guest_calls=True,
            telegram_account__isnull=False,
            user__role__in=recipient_roles,
            user__is_active=True,
            user__partner_id=partner_id,
        )
    )
    if not recipients:
        raise GuestCallError(
            "Сейчас нет сотрудников с подключёнными Telegram-уведомлениями для этого вызова."
        )

    guest_identity = (
        table_session.guest.telegram_account.username
        or str(table_session.guest.telegram_account.telegram_id)
    )
    message = (
        f"Нужен: {call_target_label}\n"
        f"Заведение: {table_session.partner.name}\n"
        f"Стол: #{table_number}\n"
        f"Гость: {guest_identity}\n"
        f"Код клиента: {table_session.guest.customer_code}"
    )
    queued_count = 0
    for employee in recipients:
        notification = StaffNotification.objects.create(
            partner_id=partner_id,
            employee=employee,
            category=StaffNotification.Category.GUEST_CALL,
            title=f"Вызов: {call_target_label}",
            message=message,
        )
        deliver_staff_notification(notification)
        queued_count += 1

    return table_number, queued_count


def notify_staff_about_parallel_visit(visit) -> int:
    """Tell staff that a new party started at a table whose previous visit is open.

    ADR-0007: the previous visit is not closed automatically; staff settle and
    close it. Recipients are the staff who receive billing requests.
    """

    recipients = list(
        EmployeeProfile.objects.select_related("telegram_account", "user").filter(
            partner_id=visit.partner_id,
            is_active=True,
            bot_notifications_enabled=True,
            notify_on_billing_requests=True,
            telegram_account__isnull=False,
            user__role__in=("waiter", "cashier", "manager", "owner"),
            user__is_active=True,
            user__partner_id=visit.partner_id,
        )
    )
    table_number = visit.table.number
    queued_count = 0
    for employee in recipients:
        notification = StaffNotification.objects.create(
            partner_id=visit.partner_id,
            employee=employee,
            category=StaffNotification.Category.TABLE_VISIT,
            title=f"Стол #{table_number}: новый визит",
            message=(
                f"За столом #{table_number} начат новый визит, предыдущий не закрыт. "
                "Рассчитайте и закройте предыдущий визит в разделе «Столы»."
            ),
        )
        deliver_staff_notification(notification)
        queued_count += 1
    return queued_count


def notify_partner_owners(*, partner_id, title: str, message: str) -> int:
    """A security alert to the venue owners linked to the staff bot.

    Owners are the users with an active owner admin profile or the owner role.
    The per-topic notification switches do not apply: the owner must know
    when payment details or rights change (AA-035).
    """

    recipients = EmployeeProfile.objects.filter(
        Q(user__admin_access_profile__is_partner_owner=True)
        | Q(user__role="owner"),
        partner_id=partner_id,
        is_active=True,
        telegram_account__isnull=False,
        user__is_active=True,
        user__partner_id=partner_id,
    ).distinct()
    queued_count = 0
    for employee in recipients:
        notification = StaffNotification.objects.create(
            partner_id=partner_id,
            employee=employee,
            category=StaffNotification.Category.SECURITY,
            title=title,
            message=message,
        )
        deliver_staff_notification(notification)
        queued_count += 1
    return queued_count


def notify_owners_about_transfer_details_change(*, settings, actor) -> int:
    who = getattr(actor, "username", "") or "неизвестно"
    details = settings.transfer_payment_details or "(пусто)"
    return notify_partner_owners(
        partner_id=settings.partner_id,
        title="Изменены реквизиты для оплаты",
        message=(
            f"Реквизиты для перевода изменил пользователь {who}.\n"
            f"Новые реквизиты: {details}\n"
            "Если это сделали не вы — сразу верните реквизиты, смените пароль и "
            "проверьте доступы в Admin."
        ),
    )


def notify_staff_about_billing_request(billing_request) -> int:
    recipients = list(
        EmployeeProfile.objects.select_related("telegram_account", "user").filter(
            partner_id=billing_request.partner_id,
            is_active=True,
            bot_notifications_enabled=True,
            notify_on_billing_requests=True,
            telegram_account__isnull=False,
            user__role__in=("waiter", "cashier", "manager", "owner"),
            user__is_active=True,
            user__partner_id=billing_request.partner_id,
        )
    )
    if not recipients:
        return 0

    request_type_label = billing_request.get_request_type_display()
    guest_identity = (
        billing_request.guest.telegram_account.username
        or str(billing_request.guest.telegram_account.telegram_id)
    )
    lines = [
        f"Тип: {request_type_label}",
        f"Стол: #{billing_request.table.number}",
        f"Гость: {guest_identity}",
        f"Код клиента: {billing_request.guest.customer_code}",
    ]
    if billing_request.bill_id:
        lines.append(f"Подготовлен счёт: #{billing_request.bill.public_id}")
    lines.append(f"Запрос: #{billing_request.id}")
    message = "\n".join(lines)

    queued_count = 0
    for employee in recipients:
        notification = StaffNotification.objects.create(
            partner_id=billing_request.partner_id,
            employee=employee,
            category=StaffNotification.Category.BILLING_REQUEST,
            title="Запрос счёта",
            message=message,
        )
        deliver_staff_notification(notification)
        queued_count += 1
    return queued_count


@transaction.atomic
def send_broadcast_campaign(campaign: BroadcastCampaign) -> BroadcastCampaign:
    """Queue a campaign. Re-queueing reaches only guests not yet attempted."""

    campaign = BroadcastCampaign.objects.select_for_update().get(id=campaign.id)
    if campaign.status in {
        BroadcastCampaign.Status.SCHEDULED,
        BroadcastCampaign.Status.SENDING,
        BroadcastCampaign.Status.SENT,
    }:
        return campaign

    campaign.status = BroadcastCampaign.Status.SCHEDULED
    campaign.delivery_started_at = None
    campaign.sent_at = None
    campaign.last_error = ""
    campaign.save(
        update_fields=[
            "status",
            "delivery_started_at",
            "sent_at",
            "last_error",
            "updated_at",
        ]
    )
    _enqueue_broadcast_task(campaign.id)
    return campaign


def _enqueue_broadcast_task(campaign_id) -> None:
    from apps.notifications.tasks import send_broadcast_campaign_task

    transaction.on_commit(lambda: send_broadcast_campaign_task.delay(str(campaign_id), ""))


def send_broadcast_campaign_now(
    campaign: BroadcastCampaign,
    *,
    after_guest_id: str = "",
) -> BroadcastCampaign:
    """Send one batch of a campaign; queue the next batch if work remains.

    Delivery contract (PQ-014, AA-008): every guest receives a campaign at most
    once. The audience is recorded as ``BroadcastDelivery`` rows when sending
    starts; a batch is claimed in a short transaction, sent with no
    transaction open, and every outcome is stored in its own short
    transaction. Duplicate or retried tasks and parallel workers only claim
    rows that are still ``pending``.

    ``after_guest_id`` is accepted for tasks queued before this contract (the
    old cursor); it limits the recorded audience to guests after it.
    """

    started = _start_broadcast(campaign.id, legacy_after_guest_id=after_guest_id)
    if started is None:
        return BroadcastCampaign.objects.get(id=campaign.id)

    try:
        bot_instance = _active_partner_bot(started.partner_id)
    except NotificationDeliveryError as exc:
        return _fail_broadcast(started.id, str(exc))

    claim_token, deliveries = _claim_broadcast_batch(started.id)
    if deliveries:
        _send_claimed_broadcast_batch(
            campaign=started,
            token=bot_instance.token,
            claim_token=claim_token,
            deliveries=deliveries,
        )

    return _advance_broadcast(started.id)


@transaction.atomic
def _start_broadcast(campaign_id, *, legacy_after_guest_id: str = ""):
    """Lock the campaign, record its audience once, return it if sendable."""

    campaign = BroadcastCampaign.objects.select_for_update().get(id=campaign_id)
    if campaign.status in {BroadcastCampaign.Status.SENT, BroadcastCampaign.Status.FAILED}:
        return None

    has_deliveries = campaign.deliveries.exists()
    if campaign.status != BroadcastCampaign.Status.SENDING:
        _record_broadcast_audience(campaign)
        campaign.status = BroadcastCampaign.Status.SENDING
        campaign.delivery_started_at = timezone.now()
        campaign.last_error = ""
        campaign.sent_at = None
        campaign.save(
            update_fields=[
                "status",
                "delivery_started_at",
                "last_error",
                "sent_at",
                "updated_at",
            ]
        )
    elif not has_deliveries:
        # A campaign that was mid-way under the old cursor-based sender.
        if legacy_after_guest_id:
            _record_broadcast_audience(campaign, after_guest_id=legacy_after_guest_id)
        elif campaign.delivered_count or campaign.failed_count:
            _fail_locked_broadcast(
                campaign,
                "Sending was interrupted by the delivery upgrade; not resumed to "
                "avoid duplicate messages.",
            )
            return None
        else:
            _record_broadcast_audience(campaign)

    if not campaign.deliveries.exists():
        _fail_locked_broadcast(campaign, "No eligible recipients for this campaign.")
        return None
    return campaign


def _record_broadcast_audience(campaign: BroadcastCampaign, *, after_guest_id: str = "") -> int:
    created = 0
    cursor = after_guest_id or None
    while True:
        guests = NotificationPreferenceRepository.marketing_guests_batch(
            campaign.partner_id,
            after_guest_id=cursor,
            limit=1000,
        )
        if not guests:
            return created
        BroadcastDelivery.objects.bulk_create(
            [
                BroadcastDelivery(
                    partner_id=campaign.partner_id,
                    campaign=campaign,
                    guest=guest,
                )
                for guest in guests
            ],
            ignore_conflicts=True,
        )
        created += len(guests)
        cursor = str(guests[-1].id)


def _fail_broadcast(campaign_id, error: str) -> BroadcastCampaign:
    with transaction.atomic():
        campaign = BroadcastCampaign.objects.select_for_update().get(id=campaign_id)
        if campaign.status != BroadcastCampaign.Status.SENDING:
            return campaign
        _fail_locked_broadcast(campaign, error)
        return campaign


def _fail_locked_broadcast(campaign: BroadcastCampaign, error: str) -> None:
    _refresh_broadcast_counters(campaign)
    campaign.status = BroadcastCampaign.Status.FAILED
    campaign.last_error = error
    campaign.save(
        update_fields=[
            "status",
            "last_error",
            "delivered_count",
            "failed_count",
            "unknown_count",
            "updated_at",
        ]
    )


@transaction.atomic
def _claim_broadcast_batch(campaign_id):
    claim_token = uuid.uuid4()
    pending_ids = list(
        BroadcastDelivery.objects.select_for_update(skip_locked=True)
        .filter(campaign_id=campaign_id, status=BroadcastDelivery.Status.PENDING)
        .order_by("guest_id")
        .values_list("id", flat=True)[:BROADCAST_BATCH_SIZE]
    )
    if not pending_ids:
        return claim_token, []
    BroadcastDelivery.objects.filter(
        id__in=pending_ids,
        status=BroadcastDelivery.Status.PENDING,
    ).update(
        status=BroadcastDelivery.Status.SENDING,
        claim_token=claim_token,
        claimed_at=timezone.now(),
        updated_at=timezone.now(),
    )
    deliveries = list(
        BroadcastDelivery.objects.select_related("guest__telegram_account")
        .filter(claim_token=claim_token, status=BroadcastDelivery.Status.SENDING)
        .order_by("guest_id")
    )
    return claim_token, deliveries


def _send_claimed_broadcast_batch(*, campaign, token, claim_token, deliveries) -> None:
    # Eligibility is rechecked right before sending: a guest who opted out or
    # blocked the bot since the audience was recorded is skipped.
    eligible_ids = set(
        NotificationPreferenceRepository.marketing_guests(campaign.partner_id)
        .filter(id__in=[delivery.guest_id for delivery in deliveries])
        .exclude(telegram_account__is_blocked=True)
        .values_list("id", flat=True)
    )
    to_send = []
    for delivery in deliveries:
        if delivery.guest_id in eligible_ids:
            to_send.append(delivery)
        else:
            _finish_broadcast_delivery(
                delivery.id,
                claim_token,
                status=BroadcastDelivery.Status.SKIPPED,
                error="Guest is no longer eligible for marketing messages.",
            )
    if not to_send:
        return

    by_chat_id = {delivery.guest.telegram_account.telegram_id: delivery for delivery in to_send}
    recorded: set = set()

    def record(result) -> None:
        chat_id, is_sent, error_message, should_mark_blocked = _unpack_delivery_result(result)
        delivery = by_chat_id.get(chat_id)
        if delivery is None or delivery.id in recorded:
            return
        recorded.add(delivery.id)
        if is_sent:
            status = BroadcastDelivery.Status.SENT
        elif _is_retryable_result(result):
            # Telegram may have delivered it; at most once means no resend.
            status = BroadcastDelivery.Status.UNKNOWN
        else:
            status = BroadcastDelivery.Status.FAILED
        _finish_broadcast_delivery(delivery.id, claim_token, status=status, error=error_message)
        if should_mark_blocked:
            TelegramAccount.objects.filter(telegram_id=chat_id).update(
                is_blocked=True,
                updated_at=timezone.now(),
            )

    results = async_to_sync(_send_telegram_messages)(
        token,
        [(chat_id, campaign.message) for chat_id in by_chat_id],
        on_result=sync_to_async(record),
    )
    for result in results:
        record(result)


def _finish_broadcast_delivery(delivery_id, claim_token, *, status, error: str = "") -> None:
    BroadcastDelivery.objects.filter(
        id=delivery_id,
        claim_token=claim_token,
        status=BroadcastDelivery.Status.SENDING,
    ).update(
        status=status,
        error=error,
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )


def _advance_broadcast(campaign_id) -> BroadcastCampaign:
    """Update counters; queue the next batch or finalize the campaign."""

    with transaction.atomic():
        campaign = BroadcastCampaign.objects.select_for_update().get(id=campaign_id)
        if campaign.status != BroadcastCampaign.Status.SENDING:
            return campaign
        # Rows only move from open to finished, so read the open rows first:
        # counters read afterwards include every row seen as finished here.
        open_statuses = set(
            campaign.deliveries.filter(
                status__in=[BroadcastDelivery.Status.PENDING, BroadcastDelivery.Status.SENDING]
            ).values_list("status", flat=True)
        )
        _refresh_broadcast_counters(campaign)
        if BroadcastDelivery.Status.PENDING in open_statuses:
            campaign.save(
                update_fields=["delivered_count", "failed_count", "unknown_count", "updated_at"]
            )
            _enqueue_next_broadcast_batch(campaign.id)
            return campaign
        if BroadcastDelivery.Status.SENDING in open_statuses:
            # Another worker still owns a batch; it finalizes when done, and
            # recovery handles it if that worker died.
            campaign.save(
                update_fields=["delivered_count", "failed_count", "unknown_count", "updated_at"]
            )
            return campaign
        _finalize_locked_broadcast(campaign)
        return campaign


def _enqueue_next_broadcast_batch(campaign_id) -> None:
    from apps.notifications.tasks import send_broadcast_campaign_task

    transaction.on_commit(lambda: send_broadcast_campaign_task.delay(str(campaign_id), ""))


def _refresh_broadcast_counters(campaign: BroadcastCampaign) -> None:
    counts = campaign.deliveries.aggregate(
        sent=Count("id", filter=Q(status=BroadcastDelivery.Status.SENT)),
        failed=Count("id", filter=Q(status=BroadcastDelivery.Status.FAILED)),
        unknown=Count("id", filter=Q(status=BroadcastDelivery.Status.UNKNOWN)),
    )
    campaign.delivered_count = counts["sent"]
    campaign.failed_count = counts["failed"]
    campaign.unknown_count = counts["unknown"]
    last_failure = (
        campaign.deliveries.filter(
            status__in=[BroadcastDelivery.Status.FAILED, BroadcastDelivery.Status.UNKNOWN]
        )
        .exclude(error="")
        .order_by("-finished_at")
        .values_list("error", flat=True)
        .first()
    )
    if last_failure:
        campaign.last_error = last_failure


def _finalize_locked_broadcast(campaign: BroadcastCampaign) -> None:
    campaign.sent_at = timezone.now()
    if campaign.delivered_count == 0:
        campaign.status = BroadcastCampaign.Status.FAILED
        if not campaign.last_error:
            campaign.last_error = "Campaign delivery failed for all recipients."
    else:
        campaign.status = BroadcastCampaign.Status.SENT
    campaign.save(
        update_fields=[
            "sent_at",
            "status",
            "last_error",
            "delivered_count",
            "failed_count",
            "unknown_count",
            "updated_at",
        ]
    )


def recover_stalled_broadcast_campaigns(*, now=None) -> int:
    """Periodic recovery for broadcasts whose worker or task was lost.

    Expired ``sending`` claims become ``unknown`` (never resent). A sending
    campaign with pending rows and no recent claim gets a new task; one with
    no open rows is finalized. Returns the number of campaigns touched.
    """

    now = now or timezone.now()
    expired_before = now - BROADCAST_CLAIM_LEASE
    BroadcastDelivery.objects.filter(
        status=BroadcastDelivery.Status.SENDING,
        claimed_at__lt=expired_before,
    ).update(
        status=BroadcastDelivery.Status.UNKNOWN,
        error="The worker stopped before recording the result; not resent.",
        finished_at=now,
        updated_at=now,
    )
    touched = 0
    sending = BroadcastCampaign.objects.filter(status=BroadcastCampaign.Status.SENDING).annotate(
        last_claim=Max("deliveries__claimed_at"),
        open_sending=Count(
            "deliveries",
            filter=Q(deliveries__status=BroadcastDelivery.Status.SENDING),
        ),
    )
    for campaign in sending:
        if campaign.open_sending:
            continue
        last_activity = campaign.last_claim or campaign.delivery_started_at or campaign.updated_at
        if last_activity and last_activity >= expired_before:
            continue
        _advance_broadcast(campaign.id)
        touched += 1
    return touched


def process_scheduled_broadcast_campaigns() -> int:
    processed_count = 0
    for campaign in BroadcastCampaignRepository.due_scheduled():
        send_broadcast_campaign(campaign)
        processed_count += 1
    return processed_count
