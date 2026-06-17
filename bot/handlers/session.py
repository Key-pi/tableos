from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.orders.models import Order
from apps.orders.selectors import get_orders_for_session
from apps.orders.services import (
    OrderFlowError,
    confirm_order_received_by_guest,
    is_order_final,
    is_order_paid,
    is_order_received,
)
from apps.tables.services import get_active_table_session
from bot.filters.content import PartnerButtonFilter
from bot.keyboards.main import build_main_keyboard
from bot.keyboards.session import (
    build_guest_order_card_keyboard,
    build_guest_session_overview_keyboard,
)
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state

router = Router()


def _render_guest_order_line(order: Order) -> str:
    payment_label = "оплачен" if is_order_paid(order) else "не оплачен"
    received_label = "получен" if is_order_received(order) else "не получен"
    return (
        f"• #{order.public_id} — {order.get_status_display()} — "
        f"{order.total_amount} грн — {payment_label} — {received_label}"
    )


def _build_guest_order_shortcuts(orders: list[Order]) -> list[tuple[str, str]]:
    visible_orders = [order for order in orders if not is_order_final(order)]
    shortcuts: list[tuple[str, str]] = []
    for order in visible_orders[:8]:
        payment_badge = "не опл." if not is_order_paid(order) else "опл."
        shortcuts.append(
            (
                order.public_id,
                f"#{order.public_id} • {order.get_status_display()} • {payment_badge}",
            )
        )
    return shortcuts


def _build_guest_session_view(
    *,
    partner,
    session,
    orders,
    content,
) -> tuple[str, list[tuple[str, str]]]:
    visible_orders = [order for order in orders if not is_order_final(order)]
    preview_orders = visible_orders[:6]
    lines = [
        f"Ваш стол: #{session.table.number}",
        f"Заведение: {partner.name}",
    ]

    if not visible_orders:
        lines.extend(
            [
                "",
                "По вашему столу сейчас нет незавершённых заказов.",
            ]
        )
        lines.extend(content.ordering_entry_lines(table_number=session.table.number))
        return "\n".join(lines), []

    unpaid_count = sum(1 for order in visible_orders if not is_order_paid(order))
    unreceived_count = sum(1 for order in visible_orders if not is_order_received(order))
    lines.extend(
        [
            "",
            f"Незавершённых заказов: {len(visible_orders)}",
            f"Не оплачено: {unpaid_count}",
            f"Не получено: {unreceived_count}",
            "",
            "Ваши заказы:",
        ]
    )
    lines.extend(_render_guest_order_line(order) for order in preview_orders)
    hidden_orders_count = len(visible_orders) - len(preview_orders)
    if hidden_orders_count > 0:
        lines.append(f"• И ещё заказов: {hidden_orders_count}")

    if unpaid_count:
        lines.extend(
            [
                "",
                (
                    "Если заказ уже у вас, но он ещё не оплачен, "
                    "запросите счёт кнопкой «Запросить счёт»."
                ),
            ]
        )

    return "\n".join(lines), _build_guest_order_shortcuts(visible_orders)


def _employee_label(order: Order) -> str:
    if not order.assigned_employee_id:
        return "ещё не назначен"
    full_name = order.assigned_employee.user.get_full_name().strip()
    return full_name or order.assigned_employee.user.username


def _build_guest_order_card_text(order: Order) -> str:
    payment_label = "ещё не зафиксирована" if not is_order_paid(order) else "уже зафиксирована"
    received_label = "ещё не подтверждено" if not is_order_received(order) else "уже подтверждено"
    lines = [
        f"<b>Заказ #{order.public_id}</b>",
        f"Стол: #{order.table.number}",
        f"Новый статус: {order.get_status_display()}",
        f"Оплата: {payment_label}",
        f"Получение: {received_label}",
        f"Заказ взял в работу: {_employee_label(order)}",
    ]
    if not is_order_paid(order):
        lines.extend(
            [
                "",
                (
                    "Если заказ уже у вас, но он ещё не оплачен, "
                    "запросите счёт кнопкой «Запросить счёт»."
                ),
            ]
        )
    if order.status not in {Order.Status.NEW, Order.Status.CANCELED, Order.Status.COMPLETED}:
        lines.extend(
            [
                "",
                "Если заказ уже получили, подтвердите это кнопкой ниже.",
            ]
        )
    return "\n".join(lines)


async def _safe_edit_session_message(message: Message, text: str, *, reply_markup) -> bool:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return False
        raise


@router.message(PartnerButtonFilter("button_session_label"))
async def session_handler(message: Message, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    if not content.supports_tables():
        navigation_state = await sync_to_async(resolve_guest_navigation_state)(
            partner_id=partner.id,
            telegram_id=message.from_user.id,
            content=content,
        )
        await message.answer(
            "Столы сейчас отключены для этого бота.",
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
    )
    if session is None:
        navigation_state = await sync_to_async(resolve_guest_navigation_state)(
            partner_id=partner.id,
            telegram_id=message.from_user.id,
            content=content,
        )
        await message.answer(
            "Активной сессии нет. Сначала отсканируйте QR-код на столе.",
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return

    orders = await sync_to_async(list)(get_orders_for_session(session.id))
    session_text, order_shortcuts = await sync_to_async(_build_guest_session_view)(
        partner=partner,
        session=session,
        orders=orders,
        content=content,
    )
    await message.answer(
        session_text,
        reply_markup=build_guest_session_overview_keyboard(
            order_shortcuts=order_shortcuts,
        ),
    )


@router.callback_query(F.data.startswith("guestorderopen:"))
async def guest_order_open_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    order_public_id = (callback.data or "").split(":", 1)[1]
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
    )
    if session is None:
        await callback.answer("Активная сессия уже закрыта.", show_alert=True)
        return

    orders = await sync_to_async(list)(get_orders_for_session(session.id))
    try:
        order = next(order for order in orders if order.public_id == order_public_id)
    except StopIteration:
        await callback.answer("Заказ уже не найден для активного стола.", show_alert=True)
        return

    updated = await _safe_edit_session_message(
        callback.message,
        _build_guest_order_card_text(order),
        reply_markup=build_guest_order_card_keyboard(
            order_public_id=order.public_id,
            can_confirm_received=order.status
            not in {Order.Status.NEW, Order.Status.CANCELED, Order.Status.COMPLETED},
        ),
    )
    if updated:
        await callback.answer()
    else:
        await callback.answer("Без изменений")


@router.callback_query(F.data == "guestsession:back")
async def guest_session_back_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
    )
    if session is None:
        await callback.answer("Активная сессия уже закрыта.", show_alert=True)
        return

    orders = await sync_to_async(list)(get_orders_for_session(session.id))
    session_text, order_shortcuts = await sync_to_async(_build_guest_session_view)(
        partner=partner,
        session=session,
        orders=orders,
        content=content,
    )
    updated = await _safe_edit_session_message(
        callback.message,
        session_text,
        reply_markup=build_guest_session_overview_keyboard(
            order_shortcuts=order_shortcuts,
        ),
    )
    if updated:
        await callback.answer()
    else:
        await callback.answer("Без изменений")


@router.callback_query(F.data.startswith("guestorderconfirm:"))
async def guest_order_confirm_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    order_public_id = (callback.data or "").split(":", 1)[1]

    try:
        order = await sync_to_async(confirm_order_received_by_guest)(
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            order_public_id=order_public_id,
        )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
    )
    if session is None:
        await callback.message.edit_text("Активная сессия уже закрыта.")
        await callback.answer("Получение подтверждено.")
        return

    updated = await _safe_edit_session_message(
        callback.message,
        _build_guest_order_card_text(order),
        reply_markup=build_guest_order_card_keyboard(
            order_public_id=order.public_id,
            can_confirm_received=False,
        ),
    )
    if updated:
        await callback.answer(f"Заказ #{order.public_id} отмечен как полученный.")
    else:
        await callback.answer("Без изменений")
