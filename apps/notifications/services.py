from __future__ import annotations

import asyncio
from datetime import timedelta

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from asgiref.sync import async_to_sync
from django.db import transaction
from django.utils import timezone

from apps.employees.models import EmployeeProfile
from apps.notifications.models import BroadcastCampaign, StaffNotification
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
BROADCAST_BATCH_SIZE = max(1, app_settings.notification_broadcast_batch_size)


class NotificationDeliveryError(Exception):
    """Raised when partner bot delivery cannot be performed safely."""


class GuestCallError(Exception):
    """Raised when a guest cannot safely request staff assistance."""


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
    messages: list[tuple[int, str]],
) -> list[tuple[int, bool, str, bool]]:
    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    results: list[tuple[int, bool, str, bool]] = []
    try:
        for chat_id, text in messages:
            while True:
                try:
                    await bot.send_message(chat_id=chat_id, text=text)
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(max(float(exc.retry_after), 1.0))
                    continue
                except TelegramForbiddenError as exc:
                    results.append((chat_id, False, str(exc), True))
                except TelegramBadRequest as exc:
                    results.append((chat_id, False, str(exc), False))
                except TelegramAPIError as exc:
                    results.append((chat_id, False, str(exc), False))
                except Exception as exc:  # noqa: BLE001
                    results.append((chat_id, False, str(exc), False))
                else:
                    results.append((chat_id, True, "", False))
                break
    finally:
        await bot.session.close()
    return results


def _unpack_delivery_result(result) -> tuple[int, bool, str, bool]:
    chat_id = result[0]
    is_sent = result[1]
    error_message = result[2]
    should_mark_blocked = result[3] if len(result) > 3 else False
    return chat_id, is_sent, error_message, should_mark_blocked


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
    employee = notification.employee
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
                    f"<b>{notification.title}</b>\n{notification.message}",
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
    if order.table_id is not None:
        return f"Стол: #{order.table.number}"
    return "Сценарий: заказ без привязки к столу"


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
        lines.append(f"Заказ взял в работу: {_employee_display_name(order.assigned_employee)}")
    elif order.assigned_employee_id:
        lines.append(f"Ответственный: {_employee_display_name(order.assigned_employee)}")

    if order.comment:
        lines.append(f"Комментарий: {order.comment}")

    results = async_to_sync(_send_telegram_messages)(
        bot_instance.token,
        [(guest_chat_id, "\n".join(lines))],
    )
    _chat_id, is_sent, _error_message, should_mark_blocked = _unpack_delivery_result(results[0])
    if should_mark_blocked:
        order.guest.telegram_account.is_blocked = True
        order.guest.telegram_account.save(update_fields=["is_blocked", "updated_at"])
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


def notify_staff_about_billing_request(billing_request) -> int:
    recipients = list(
        EmployeeProfile.objects.select_related("telegram_account", "user").filter(
            partner_id=billing_request.partner_id,
            is_active=True,
            bot_notifications_enabled=True,
            notify_on_billing_requests=True,
            telegram_account__isnull=False,
            user__role__in=("waiter", "cashier", "manager", "owner"),
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
    if campaign.status in {
        BroadcastCampaign.Status.SCHEDULED,
        BroadcastCampaign.Status.SENDING,
        BroadcastCampaign.Status.SENT,
    }:
        return campaign

    campaign.status = BroadcastCampaign.Status.SCHEDULED
    campaign.delivery_started_at = None
    campaign.sent_at = None
    campaign.delivered_count = 0
    campaign.failed_count = 0
    campaign.last_error = ""
    campaign.save(
        update_fields=[
            "status",
            "delivery_started_at",
            "sent_at",
            "delivered_count",
            "failed_count",
            "last_error",
            "updated_at",
        ]
    )
    from apps.notifications.tasks import send_broadcast_campaign_task

    transaction.on_commit(
        lambda: send_broadcast_campaign_task.delay(str(campaign.id), "")
    )
    return campaign


@transaction.atomic
def send_broadcast_campaign_now(
    campaign: BroadcastCampaign,
    *,
    after_guest_id: str = "",
) -> BroadcastCampaign:
    recipient_batch = NotificationPreferenceRepository.marketing_guests_batch(
        campaign.partner_id,
        after_guest_id=after_guest_id or None,
        limit=BROADCAST_BATCH_SIZE + 1,
    )
    has_more_batches = len(recipient_batch) > BROADCAST_BATCH_SIZE
    recipients = recipient_batch[:BROADCAST_BATCH_SIZE]

    if after_guest_id:
        campaign = BroadcastCampaign.objects.get(id=campaign.id)
    elif campaign.status != BroadcastCampaign.Status.SENDING:
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

    if not recipients:
        if after_guest_id:
            _finalize_broadcast_campaign(campaign)
        else:
            campaign.status = BroadcastCampaign.Status.FAILED
            campaign.delivered_count = 0
            campaign.failed_count = 0
            campaign.last_error = "No eligible recipients for this campaign."
            campaign.save(
                update_fields=[
                    "status",
                    "delivered_count",
                    "failed_count",
                    "last_error",
                    "updated_at",
                ]
            )
        return campaign

    try:
        bot_instance = _active_partner_bot(campaign.partner_id)
    except NotificationDeliveryError as exc:
        campaign.status = BroadcastCampaign.Status.FAILED
        campaign.last_error = str(exc)
        campaign.failed_count = 0
        campaign.save(update_fields=["status", "last_error", "failed_count", "updated_at"])
        return campaign

    message_payloads = [
        (guest.telegram_account.telegram_id, campaign.message)
        for guest in recipients
    ]
    results = async_to_sync(_send_telegram_messages)(bot_instance.token, message_payloads)
    delivered_count = 0
    failed_count = 0
    failed_results: list[tuple[int, bool, str, bool]] = []
    blocked_chat_ids: list[int] = []
    for result in results:
        chat_id, is_sent, error_message, should_mark_blocked = _unpack_delivery_result(result)
        if is_sent:
            delivered_count += 1
        else:
            failed_count += 1
            failed_results.append((chat_id, is_sent, error_message, should_mark_blocked))
        if should_mark_blocked:
            blocked_chat_ids.append(chat_id)

    if blocked_chat_ids:
        TelegramAccount.objects.filter(telegram_id__in=blocked_chat_ids).update(
            is_blocked=True,
            updated_at=timezone.now(),
        )

    campaign.delivered_count += delivered_count
    campaign.failed_count += failed_count
    if failed_results:
        campaign.last_error = failed_results[0][2]
    campaign.save(
        update_fields=[
            "delivered_count",
            "failed_count",
            "last_error",
            "updated_at",
        ]
    )

    if has_more_batches:
        from apps.notifications.tasks import send_broadcast_campaign_task

        next_after_guest_id = str(recipients[-1].id)
        transaction.on_commit(
            lambda: send_broadcast_campaign_task.delay(str(campaign.id), next_after_guest_id)
        )
        return campaign

    _finalize_broadcast_campaign(campaign)
    return campaign


def _finalize_broadcast_campaign(campaign: BroadcastCampaign) -> BroadcastCampaign:
    campaign = BroadcastCampaign.objects.get(id=campaign.id)
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
            "updated_at",
        ]
    )
    return campaign


def process_scheduled_broadcast_campaigns() -> int:
    processed_count = 0
    for campaign in BroadcastCampaignRepository.due_scheduled():
        send_broadcast_campaign(campaign)
        processed_count += 1
    return processed_count
