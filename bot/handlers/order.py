from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.orders.services import (
    OrderFlowError,
    change_cart_item_quantity_for_telegram_user,
    checkout_active_cart_for_telegram_user,
    clear_active_cart_for_telegram_user,
    get_active_cart_for_telegram_user,
    remove_cart_item_for_telegram_user,
)
from bot.filters.content import PartnerButtonFilter
from bot.keyboards.main import build_main_keyboard
from bot.keyboards.menu import build_cart_keyboard
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state

router = Router()


def _split_checkout_comment(text: str) -> str:
    payload = text.partition(" ")[2].strip()
    if not payload:
        return ""
    if payload.startswith("|"):
        return payload.removeprefix("|").strip()
    return payload


def _render_cart(cart) -> str:
    if not cart.items.all():
        return "Корзина пуста."

    lines = [
        f"<b>Корзина</b> • стол #{cart.table_session.table.number}",
    ]
    for item in cart.items.all():
        line_total = item.unit_price * item.quantity
        lines.append(f"{item.item_name} • {item.quantity} x {item.unit_price} = {line_total} грн")
    lines.append("")
    lines.append(f"Итого: {cart.total_amount} грн")
    lines.append("Используйте кнопки ниже, чтобы менять количество и оформить заказ.")
    return "\n".join(lines)


def _ensure_cart_enabled(content: BotContent) -> None:
    if not content.supports_cart():
        raise OrderFlowError(content.cart_disabled_message())


async def _send_navigation_keyboard_message(
    *,
    message: Message,
    partner_id,
    telegram_id: int,
    content: BotContent,
    text: str,
) -> None:
    await message.answer(
        text,
        reply_markup=build_main_keyboard(
            content,
            navigation_state=await sync_to_async(resolve_guest_navigation_state)(
                partner_id=partner_id,
                telegram_id=telegram_id,
                content=content,
            ),
        ),
    )


@router.message(Command("cart"))
@router.message(PartnerButtonFilter("button_cart_label"))
async def cart_handler(message: Message, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    navigation_state = await sync_to_async(resolve_guest_navigation_state)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
        content=content,
    )
    try:
        _ensure_cart_enabled(content)
    except OrderFlowError as exc:
        await message.answer(
            str(exc),
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return
    cart = await sync_to_async(get_active_cart_for_telegram_user)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
    )
    if cart is None:
        await message.answer(
            content.cart_empty_message_template,
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return

    await message.answer(
        _render_cart(cart),
        reply_markup=build_cart_keyboard(cart=cart, supports_cart=content.supports_cart()),
    )


@router.message(Command("cart_clear"))
async def clear_cart_handler(message: Message, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    navigation_state = await sync_to_async(resolve_guest_navigation_state)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
        content=content,
    )
    try:
        _ensure_cart_enabled(content)
    except OrderFlowError as exc:
        await message.answer(
            str(exc),
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return
    try:
        await sync_to_async(clear_active_cart_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=message.from_user.id,
        )
    except OrderFlowError as exc:
        await message.answer(
            str(exc),
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return

    await message.answer(
        content.cart_cleared_message_template,
        reply_markup=build_main_keyboard(
            content,
            navigation_state=await sync_to_async(resolve_guest_navigation_state)(
                partner_id=partner.id,
                telegram_id=message.from_user.id,
                content=content,
            ),
        ),
    )


@router.message(Command("checkout"))
@router.message(PartnerButtonFilter("button_checkout_label"))
async def checkout_handler(message: Message, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    navigation_state = await sync_to_async(resolve_guest_navigation_state)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
        content=content,
    )
    try:
        _ensure_cart_enabled(content)
    except OrderFlowError as exc:
        await message.answer(
            str(exc),
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return
    try:
        order = await sync_to_async(checkout_active_cart_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=message.from_user.id,
            comment=_split_checkout_comment(message.text or ""),
        )
    except OrderFlowError as exc:
        await message.answer(
            str(exc),
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return

    await message.answer(
        content.order_created_message(
            order_public_id=order.public_id,
            total_amount=order.total_amount,
            status_display=order.get_status_display(),
        ),
        reply_markup=build_main_keyboard(
            content,
            navigation_state=await sync_to_async(resolve_guest_navigation_state)(
                partner_id=partner.id,
                telegram_id=message.from_user.id,
                content=content,
            ),
        ),
    )


@router.message(PartnerButtonFilter("button_help_label"))
async def ordering_help_handler(message: Message, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    await message.answer(
        content.ordering_help_message(),
        reply_markup=build_main_keyboard(
            content,
            navigation_state=await sync_to_async(resolve_guest_navigation_state)(
                partner_id=partner.id,
                telegram_id=message.from_user.id,
                content=content,
            ),
        ),
    )


@router.callback_query(F.data == "cart:refresh")
async def cart_refresh_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    try:
        _ensure_cart_enabled(content)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    cart = await sync_to_async(get_active_cart_for_telegram_user)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
    )
    if cart is None:
        await callback.message.edit_text(content.cart_empty_message_template)
        await _send_navigation_keyboard_message(
            message=callback.message,
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            content=content,
            text="Клавиатура обновлена.",
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        _render_cart(cart),
        reply_markup=build_cart_keyboard(cart=cart, supports_cart=True),
    )
    await callback.answer()


@router.callback_query(F.data == "cart:clear")
async def cart_clear_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    try:
        _ensure_cart_enabled(content)
        await sync_to_async(clear_active_cart_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
        )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.message.edit_text(content.cart_cleared_message_template)
    await _send_navigation_keyboard_message(
        message=callback.message,
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
        content=content,
        text="Клавиатура обновлена.",
    )
    await callback.answer("Корзина очищена.")


@router.callback_query(F.data == "cart:checkout")
async def cart_checkout_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    try:
        _ensure_cart_enabled(content)
        order = await sync_to_async(checkout_active_cart_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
        )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.message.edit_text(
        content.order_created_message(
            order_public_id=order.public_id,
            total_amount=order.total_amount,
            status_display=order.get_status_display(),
        )
    )
    await _send_navigation_keyboard_message(
        message=callback.message,
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
        content=content,
        text="Клавиатура обновлена.",
    )
    await callback.answer("Заказ оформлен.")


@router.callback_query(F.data.startswith("cart:item:noop:"))
async def cart_item_noop_callback(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("cart:item:add:"))
@router.callback_query(F.data.startswith("cart:item:sub:"))
async def cart_item_quantity_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    _prefix, _scope, action, item_public_id = (callback.data or "").split(":", 3)
    delta = 1 if action == "add" else -1
    try:
        _ensure_cart_enabled(content)
        cart = await sync_to_async(change_cart_item_quantity_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            item_public_id=item_public_id,
            delta=delta,
        )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if not cart.items.all():
        await callback.message.edit_text(content.cart_empty_message_template)
        await _send_navigation_keyboard_message(
            message=callback.message,
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            content=content,
            text="Клавиатура обновлена.",
        )
        await callback.answer("Корзина обновлена.")
        return

    await callback.message.edit_text(
        _render_cart(cart),
        reply_markup=build_cart_keyboard(cart=cart, supports_cart=True),
    )
    await callback.answer("Корзина обновлена.")


@router.callback_query(F.data.startswith("cart:item:remove:"))
async def cart_item_remove_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    _prefix, _scope, _action, item_public_id = (callback.data or "").split(":", 3)
    try:
        _ensure_cart_enabled(content)
        cart = await sync_to_async(remove_cart_item_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            item_public_id=item_public_id,
        )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if not cart.items.all():
        await callback.message.edit_text(content.cart_empty_message_template)
        await _send_navigation_keyboard_message(
            message=callback.message,
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            content=content,
            text="Клавиатура обновлена.",
        )
        await callback.answer("Позиция удалена.")
        return

    await callback.message.edit_text(
        _render_cart(cart),
        reply_markup=build_cart_keyboard(cart=cart, supports_cart=True),
    )
    await callback.answer("Позиция удалена.")
