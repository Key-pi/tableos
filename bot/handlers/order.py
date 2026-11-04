from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from asgiref.sync import sync_to_async

from apps.billing.remote_orders import (
    RemoteOrderError,
    available_slots,
    closed_message,
    get_delivery_terms,
    get_shipping_terms,
)
from apps.orders.models import Order
from apps.orders.services import (
    CHANNEL_OFF_MESSAGES,
    CHOOSE_WAY_FIRST,
    OrderFlowError,
    change_cart_item_quantity_for_telegram_user,
    checkout_active_cart_for_telegram_user,
    clear_active_cart_for_telegram_user,
    get_active_cart_for_telegram_user,
    remove_cart_item_for_telegram_user,
    switch_cart_channel_for_telegram_user,
)
from apps.tables.services import get_active_table_session
from bot.filters.content import PartnerButtonFilter
from bot.handlers.menu import choose_way_buttons, send_menu_view
from bot.handlers.remote_checkout import start_remote_checkout
from bot.keyboards.main import build_main_keyboard
from bot.keyboards.menu import build_cart_keyboard
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state
from bot.utils.html import escape_html
from core.i18n import N_, _

router = Router()


def _cart_place(cart) -> str:
    if cart.channel == Order.Channel.PICKUP:
        return _("самовывоз")
    if cart.channel == Order.Channel.DELIVERY:
        return _("доставка")
    if cart.channel == Order.Channel.SHIPPING:
        return _("отправка почтой")
    if cart.table_session is not None:
        return _("стол #{number}", number=cart.table_session.table.number)
    return _("способ не выбран")


def _render_cart(cart, *, removed: list[str] | None = None) -> str:
    removed_line = (
        [
            _(
                "Убрали — недоступно для этого способа заказа: {items}",
                items=escape_html(", ".join(removed)),
            )
        ]
        if removed
        else []
    )
    if not cart.items.all():
        return "\n".join(
            [
                _("<b>Корзина</b> • {cart_place}", cart_place=_cart_place(cart)),
                _("Корзина пуста."),
                *removed_line,
            ]
        )

    preview_items = list(cart.items.all()[:6])
    lines = [
        _("<b>Корзина</b> • {cart_place}", cart_place=_cart_place(cart)),
        *removed_line,
    ]
    for item in preview_items:
        line_total = item.unit_price * item.quantity
        lines.append(
            _(
                "{item_name} × {quantity} — {line_total} грн",
                item_name=escape_html(item.item_name),
                quantity=item.quantity,
                unit_price=item.unit_price,
                line_total=line_total,
            )
        )
    hidden_items_count = cart.items.count() - len(preview_items)
    if hidden_items_count > 0:
        lines.append(
            _("И ещё позиций: {hidden_items_count}", hidden_items_count=hidden_items_count)
        )
    lines.append("")
    lines.append(_("Итого: <b>{total_amount} грн</b>", total_amount=cart.total_amount))
    if cart.channel == Order.Channel.TABLE and cart.table_session is None:
        lines.append(_("Выберите ниже, как получите заказ."))
    return "\n".join(lines)


async def _safe_edit_cart_message(message: Message, text: str, *, reply_markup=None) -> bool:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return False
        raise


async def _has_active_session(partner, content: BotContent, telegram_id: int) -> bool:
    if not content.supports_tables():
        return False
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=telegram_id,
    )
    return session is not None


def _mode_switch(cart, content: BotContent, *, at_table: bool):
    """At a table the cart may switch to pickup and back to the table."""

    if cart.channel == Order.Channel.TABLE and cart.table_session is not None:
        if content.supports_pickup_orders():
            return (_("🥡 Взять с собой (самовывоз)"), "cart:mode:pickup")
    elif at_table and cart.channel in Order.REMOTE_CHANNELS:
        return (_("↩ Заказать к столу"), "cart:mode:table")
    return None


async def _cart_view(cart, partner, content: BotContent, telegram_id: int, *, removed=None):
    at_table = await _has_active_session(partner, content, telegram_id)
    unchosen = cart.channel == Order.Channel.TABLE and cart.table_session is None
    return (
        _render_cart(cart, removed=removed),
        build_cart_keyboard(
            cart=cart,
            supports_cart=content.supports_cart(),
            mode_switch=_mode_switch(cart, content, at_table=at_table),
            choose_way=choose_way_buttons(content) if unchosen else (),
        ),
    )


async def _route_checkout(target, bot: Bot, partner, content: BotContent) -> bool:
    """Checkout follows the flow the guest chose (the cart's channel).

    Returns True when the pickup flow took over; False means a table checkout.
    """

    cart = await sync_to_async(get_active_cart_for_telegram_user)(
        partner_id=partner.id,
        telegram_id=target.from_user.id,
    )
    if cart is not None and cart.channel in Order.PREPAID_CHANNELS:
        await start_remote_checkout(target, bot, cart.channel)
        return True
    if cart is not None and cart.table_session_id is None:
        # The guest left the table: the cart waits for a way of ordering.
        ways = choose_way_buttons(content)
        message = target.message if isinstance(target, CallbackQuery) else target
        await message.answer(
            _(CHOOSE_WAY_FIRST),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=text, callback_data=data)] for text, data in ways
                ]
            )
            if ways
            else None,
        )
        return True
    return False


def _ensure_cart_enabled(content: BotContent) -> None:
    if not content.supports_cart():
        raise OrderFlowError(content.cart_disabled_message())


async def _send_navigation_keyboard_message(
    *,
    message: Message,
    partner_id,
    telegram_id: int,
    content: BotContent,
    text: str | None = None,
) -> None:
    keyboard_message = await message.answer(
        text or "\u2060",
        reply_markup=build_main_keyboard(
            content,
            navigation_state=await sync_to_async(resolve_guest_navigation_state)(
                partner_id=partner_id,
                telegram_id=telegram_id,
                content=content,
            ),
        ),
    )
    if text is None:
        try:
            await keyboard_message.delete()
        except TelegramBadRequest:
            pass


# Remote flows chosen by a main-menu button (ADR-0009): the cart switches to
# the channel and the menu shows only what is sold there.
REMOTE_FLOW_INTROS = {
    Order.Channel.PICKUP: (N_("<b>Самовывоз</b>"), N_("не продаётся на вынос")),
    Order.Channel.DELIVERY: (N_("<b>Доставка</b>"), N_("не доставляем")),
    Order.Channel.SHIPPING: (N_("<b>Отправка почтой</b>"), N_("не отправляем почтой")),
}


def _remote_flow_supported(content: BotContent, channel: str) -> bool:
    if channel == Order.Channel.PICKUP:
        return content.supports_pickup_orders()
    if channel == Order.Channel.SHIPPING:
        return content.supports_shipping_orders()
    return content.supports_delivery_orders()


async def _choose_remote_flow(message: Message, bot: Bot, channel: str, *, user=None) -> None:
    """The guest chose pickup, delivery or shipping: a reply-keyboard button
    (``message`` is theirs) or a ``pick:`` button (``message`` is the bot's,
    ``user`` is the guest)."""

    user = user or message.from_user
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    title, removed_reason = (_(text) for text in REMOTE_FLOW_INTROS[channel])
    if not (_remote_flow_supported(content, channel) and content.supports_cart()):
        text = _(CHANNEL_OFF_MESSAGES[channel])
    elif channel != Order.Channel.SHIPPING and not await sync_to_async(available_slots)(
        partner, channel=channel
    ):
        text = await sync_to_async(closed_message)(partner, channel)
    else:
        try:
            _cart, removed = await sync_to_async(switch_cart_channel_for_telegram_user)(
                partner_id=partner.id,
                telegram_id=user.id,
                channel=channel,
            )
            if channel == Order.Channel.DELIVERY:
                terms = (await sync_to_async(get_delivery_terms)(partner.id)).describe()
            elif channel == Order.Channel.SHIPPING:
                terms = (await sync_to_async(get_shipping_terms)(partner.id)).describe()
            else:
                terms = ""
        except (OrderFlowError, RemoteOrderError) as exc:
            text = str(exc)
        else:
            lines = [title]
            if terms:
                lines.append(terms)
            if removed:
                lines.append(
                    _(
                        "Убрали из корзины — {reason}: {items}",
                        reason=removed_reason,
                        items=escape_html(", ".join(removed)),
                    )
                )
            if await _has_active_session(partner, content, user.id):
                lines.append(_("Вернуться к заказу к столу — кнопка в корзине."))
            if len(lines) > 1:
                # The menu below names the way itself; a title alone adds nothing.
                await _send_navigation_keyboard_message(
                    message=message,
                    partner_id=partner.id,
                    telegram_id=user.id,
                    content=content,
                    text="\n".join(lines),
                )
            await send_menu_view(target=message, bot=bot, user=user)
            return
    await _send_navigation_keyboard_message(
        message=message,
        partner_id=partner.id,
        telegram_id=user.id,
        content=content,
        text=text,
    )


@router.message(PartnerButtonFilter("button_delivery_label"))
async def delivery_handler(message: Message, bot: Bot) -> None:
    await _choose_remote_flow(message, bot, Order.Channel.DELIVERY)


@router.message(PartnerButtonFilter("button_shipping_label"))
async def shipping_handler(message: Message, bot: Bot) -> None:
    await _choose_remote_flow(message, bot, Order.Channel.SHIPPING)


@router.message(PartnerButtonFilter("button_pickup_label"))
async def pickup_handler(message: Message, bot: Bot) -> None:
    await _choose_remote_flow(message, bot, Order.Channel.PICKUP)


PICK_CHANNELS = {
    Order.Channel.PICKUP,
    Order.Channel.DELIVERY,
    Order.Channel.SHIPPING,
}


@router.callback_query(F.data.startswith("pick:"))
async def pick_way_callback(callback: CallbackQuery, bot: Bot) -> None:
    """A way of ordering chosen by a button under the menu or the greeting."""

    channel = (callback.data or "").split(":", 1)[1]
    if channel not in PICK_CHANNELS:
        await callback.answer()
        return
    await callback.answer()
    await _choose_remote_flow(callback.message, bot, channel, user=callback.from_user)


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

    text, markup = await _cart_view(cart, partner, content, message.from_user.id)
    await message.answer(text, reply_markup=markup)


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
    if await _route_checkout(message, bot, partner, content):
        return
    try:
        order = await sync_to_async(checkout_active_cart_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=message.from_user.id,
            # The reply button text is the configured label, never a comment
            # (AA-027); comments need an explicit input step.
            comment="",
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
        await _safe_edit_cart_message(callback.message, content.cart_empty_message_template)
        await _send_navigation_keyboard_message(
            message=callback.message,
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            content=content,
        )
        await callback.answer()
        return
    text, markup = await _cart_view(cart, partner, content, callback.from_user.id)
    updated = await _safe_edit_cart_message(callback.message, text, reply_markup=markup)
    if updated:
        await callback.answer()
    else:
        await callback.answer(_("Без изменений"))


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
    await _safe_edit_cart_message(callback.message, content.cart_cleared_message_template)
    await _send_navigation_keyboard_message(
        message=callback.message,
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
        content=content,
    )
    await callback.answer(_("Корзина очищена."))


@router.callback_query(F.data == "cart:checkout")
async def cart_checkout_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    try:
        _ensure_cart_enabled(content)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if await _route_checkout(callback, bot, partner, content):
        await callback.answer()
        return
    await _checkout_at_table(callback, partner, content)


@router.callback_query(F.data.in_({"cart:mode:pickup", "cart:mode:table"}))
async def cart_mode_callback(callback: CallbackQuery, bot: Bot) -> None:
    """At a table: take the order away (pickup) or order to the table again."""

    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    channel = (
        Order.Channel.PICKUP if callback.data == "cart:mode:pickup" else Order.Channel.TABLE
    )
    try:
        _ensure_cart_enabled(content)
        cart, removed = await sync_to_async(switch_cart_channel_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            channel=channel,
        )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    text, markup = await _cart_view(
        cart, partner, content, callback.from_user.id, removed=removed
    )
    await _safe_edit_cart_message(callback.message, text, reply_markup=markup)
    await callback.answer(
        _("Корзина: самовывоз") if channel == Order.Channel.PICKUP else _("Корзина: к столу")
    )


async def _checkout_at_table(callback: CallbackQuery, partner, content) -> None:
    try:
        _ensure_cart_enabled(content)
        order = await sync_to_async(checkout_active_cart_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
        )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _safe_edit_cart_message(
        callback.message,
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
        text=_(
            "Статус заказа — «{button_session_label}».",
            button_session_label=escape_html(content.button_session_label),
        ),
    )
    await callback.answer(_("Заказ оформлен."))


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
        await _safe_edit_cart_message(callback.message, content.cart_empty_message_template)
        await _send_navigation_keyboard_message(
            message=callback.message,
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            content=content,
        )
        await callback.answer(_("Корзина обновлена."))
        return

    text, markup = await _cart_view(cart, partner, content, callback.from_user.id)
    await _safe_edit_cart_message(callback.message, text, reply_markup=markup)
    await callback.answer(_("Корзина обновлена."))


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
        await _safe_edit_cart_message(callback.message, content.cart_empty_message_template)
        await _send_navigation_keyboard_message(
            message=callback.message,
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            content=content,
        )
        await callback.answer(_("Позиция удалена."))
        return

    text, markup = await _cart_view(cart, partner, content, callback.from_user.id)
    await _safe_edit_cart_message(callback.message, text, reply_markup=markup)
    await callback.answer(_("Позиция удалена."))
