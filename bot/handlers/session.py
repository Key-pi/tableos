from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.orders.models import Order
from apps.orders.selectors import get_orders_for_session
from apps.orders.services import (
    OrderFlowError,
    can_guest_confirm_receipt,
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
from bot.utils.html import escape_html
from core.i18n import _

router = Router()


def _render_guest_order_line(order: Order) -> str:
    payment_label = _("оплачен") if is_order_paid(order) else _("не оплачен")
    received_label = _("получен") if is_order_received(order) else _("не получен")
    return _(
        "• #{public_id} — {status} — {total_amount} грн — {payment_label} — {received_label}",
        public_id=order.public_id,
        status=order.get_status_display(),
        total_amount=order.total_amount,
        payment_label=payment_label,
        received_label=received_label,
    )


def _build_guest_order_shortcuts(orders: list[Order]) -> list[tuple[str, str]]:
    visible_orders = [order for order in orders if not is_order_final(order)]
    shortcuts: list[tuple[str, str]] = []
    for order in visible_orders[:8]:
        payment_badge = _("не опл.") if not is_order_paid(order) else _("опл.")
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
        _("Ваш стол: #{number}", number=session.table.number),
        _("Заведение: {name}", name=escape_html(partner.name)),
    ]

    if not visible_orders:
        lines.extend(["", _("Заказов пока нет.")])
        if content.supports_cart():
            lines.append(
                _(
                    "Заказать — кнопка «{button_menu_label}».",
                    button_menu_label=escape_html(content.button_menu_label),
                )
            )
        return "\n".join(lines), []

    unpaid_count = sum(1 for order in visible_orders if not is_order_paid(order))
    lines.extend(["", _("Ваши заказы:")])
    lines.extend(_render_guest_order_line(order) for order in preview_orders)
    hidden_orders_count = len(visible_orders) - len(preview_orders)
    if hidden_orders_count > 0:
        lines.append(
            _("• И ещё заказов: {hidden_orders_count}", hidden_orders_count=hidden_orders_count)
        )

    if unpaid_count and content.supports_billing_request():
        lines.extend(
            [
                "",
                _(
                    "Готовы оплатить — кнопка «{button_request_bill_label}».",
                    button_request_bill_label=escape_html(content.button_request_bill_label),
                ),
            ]
        )

    return "\n".join(lines), _build_guest_order_shortcuts(visible_orders)


def _employee_label(order: Order) -> str:
    if not order.assigned_employee_id:
        return _("ещё не назначен")
    full_name = order.assigned_employee.user.get_full_name().strip()
    return full_name or order.assigned_employee.user.username


def _build_guest_order_card_text(order: Order, content: BotContent | None = None) -> str:
    content = content or BotContent.defaults()
    lines = [
        _("<b>Заказ #{public_id}</b> · стол #{number}", public_id=order.public_id,
          number=order.table.number),
    ]
    items = getattr(order, "items", None)
    for item in items.all() if items is not None else []:
        lines.append(
            _(
                "{item_name} × {quantity}",
                item_name=escape_html(item.item_name),
                quantity=item.quantity,
            )
        )
    lines.extend(
        [
            _("Сумма: {total} грн", total=order.total_amount),
            "",
            _("Статус: {status}", status=order.get_status_display()),
            _("Оплачен") if is_order_paid(order) else _("Не оплачен"),
        ]
    )
    if is_order_received(order):
        lines.append(_("Получен"))
    if not is_order_paid(order) and content.supports_billing_request():
        lines.extend(
            [
                "",
                _(
                    "Готовы оплатить — кнопка «{button_request_bill_label}».",
                    button_request_bill_label=escape_html(content.button_request_bill_label),
                ),
            ]
        )
    if can_guest_confirm_receipt(order):
        lines.append(_("Заказ уже у вас? Подтвердите кнопкой ниже."))
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
            _("Столы сейчас отключены для этого бота."),
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
            _("Вы сейчас не за столом. Отсканируйте QR-код на столе."),
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return

    orders = await sync_to_async(list)(
        get_orders_for_session(session.id, partner_id=session.partner_id)
    )
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
    content = await sync_to_async(BotContent.for_partner)(partner)
    order_public_id = (callback.data or "").split(":", 1)[1]
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
    )
    if session is None:
        await callback.answer(_("Визит за этим столом уже закрыт."), show_alert=True)
        return

    orders = await sync_to_async(list)(
        get_orders_for_session(session.id, partner_id=session.partner_id)
    )
    try:
        order = next(order for order in orders if order.public_id == order_public_id)
    except StopIteration:
        await callback.answer(_("Заказ уже не найден для активного стола."), show_alert=True)
        return

    updated = await _safe_edit_session_message(
        callback.message,
        _build_guest_order_card_text(order, content),
        reply_markup=build_guest_order_card_keyboard(
            order_public_id=order.public_id,
            can_confirm_received=can_guest_confirm_receipt(order),
        ),
    )
    if updated:
        await callback.answer()
    else:
        await callback.answer(_("Без изменений"))


@router.callback_query(F.data == "guestsession:back")
async def guest_session_back_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
    )
    if session is None:
        await callback.answer(_("Визит за этим столом уже закрыт."), show_alert=True)
        return

    orders = await sync_to_async(list)(
        get_orders_for_session(session.id, partner_id=session.partner_id)
    )
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
        await callback.answer(_("Без изменений"))


@router.callback_query(F.data.startswith("guestorderconfirm:"))
async def guest_order_confirm_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
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
        if order.channel == Order.Channel.SHIPPING:
            # Confirmed from the «отправлен» message (PQ-034), no table.
            await callback.message.edit_text(
                _(
                    "Получение заказа #{public_id} подтверждено. Спасибо!",
                    public_id=escape_html(order.public_id),
                )
            )
        else:
            await callback.message.edit_text(_("Визит за этим столом уже закрыт."))
        await callback.answer(_("Получение подтверждено."))
        return

    updated = await _safe_edit_session_message(
        callback.message,
        _build_guest_order_card_text(order, content),
        reply_markup=build_guest_order_card_keyboard(
            order_public_id=order.public_id,
            can_confirm_received=False,
        ),
    )
    if updated:
        await callback.answer(
            _("Заказ #{public_id} отмечен как полученный.", public_id=order.public_id)
        )
    else:
        await callback.answer(_("Без изменений"))
