from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.menu.models import MenuCategory
from apps.menu.repositories import MenuItemRepository
from apps.orders.models import Order
from apps.orders.services import (
    OrderFlowError,
    add_menu_item_for_telegram_user,
    get_active_cart_for_telegram_user,
    ordering_channel_for_telegram_user,
)
from apps.tables.services import get_active_table_session
from bot.filters.content import PartnerButtonFilter
from bot.keyboards.main import build_main_keyboard
from bot.keyboards.menu import build_menu_keyboard, refresh_cart_button
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state
from bot.utils.html import escape_html
from core.i18n import N_, _

router = Router()


def _trim_menu_description(description: str, *, limit: int = 280) -> str:
    text = description.strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


async def _safe_edit_menu_message(message: Message, text: str, *, reply_markup) -> bool:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return False
        raise


# What the guest does next in the menu, by the way they chose (ADR-0009).
CHANNEL_MENU_HINTS = {
    Order.Channel.PICKUP: N_("<b>Самовывоз.</b> Нажмите на позицию, чтобы добавить её в корзину."),
    Order.Channel.DELIVERY: N_("<b>Доставка.</b> Нажмите на позицию, чтобы добавить её в корзину."),
    Order.Channel.SHIPPING: N_(
        "<b>Отправка почтой.</b> Нажмите на позицию, чтобы добавить её в корзину."
    ),
}


def choose_way_buttons(content) -> list[tuple[str, str]]:
    """Buttons that choose a remote way of ordering: (label, callback data)."""

    if not content.supports_cart():
        return []
    buttons = []
    if content.supports_pickup_orders():
        buttons.append((f"🥡 {content.button_pickup_label}", f"pick:{Order.Channel.PICKUP}"))
    if content.supports_delivery_orders():
        buttons.append((f"🚚 {content.button_delivery_label}", f"pick:{Order.Channel.DELIVERY}"))
    if content.supports_shipping_orders():
        buttons.append((f"📦 {content.button_shipping_label}", f"pick:{Order.Channel.SHIPPING}"))
    return buttons


def _ordering_hint(content, *, channel, session) -> list[str]:
    if not content.supports_cart():
        return []
    if channel == Order.Channel.TABLE and session is not None:
        return [
            content.menu_active_session_hint(table_number=session.table.number),
            _("Нажмите на позицию, чтобы добавить её в корзину."),
        ]
    if channel in CHANNEL_MENU_HINTS:
        return [_(CHANNEL_MENU_HINTS[channel])]
    if not choose_way_buttons(content):
        return [content.menu_requires_session_hint()]
    lines = [_("Чтобы заказать, выберите ниже, как получите заказ.")]
    if content.supports_table_orders():
        lines.append(_("За столом — отсканируйте QR-код на столе."))
    return lines


def _render_menu_category_view(
    *, partner, content, category, menu_items, session, channel=None
) -> str:
    lines = [content.menu_header(partner_name=partner.name)]
    lines.append(_("Категория: <b>{name}</b>", name=escape_html(category.name)))
    lines.append("")
    if not menu_items:
        lines.append(_("В этой категории сейчас нет доступных позиций."))
        lines.append("")
    else:
        for item in menu_items:
            lines.append(
                _(
                    "{name} — {price} грн",
                    name=escape_html(item.name),
                    price=item.price,
                )
            )
            if item.description:
                lines.append(f"<i>{escape_html(_trim_menu_description(item.description))}</i>")
            lines.append("")
    lines.extend(_ordering_hint(content, channel=channel, session=session))
    return "\n".join(lines).strip()


async def _answer(target, text: str, *, reply_markup) -> None:
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=reply_markup)
        await target.answer()
    else:
        await target.answer(text, reply_markup=reply_markup)


def _categories_with_items(partner_id, channel):
    """Active categories that have something to show in this way of ordering."""

    category_ids = set(
        MenuItemRepository.active_for_partner(partner_id, channel=channel).values_list(
            "category_id", flat=True
        )
    )
    return [
        category
        for category in MenuCategory.objects.filter(partner_id=partner_id, is_active=True).order_by(
            "sort_order", "name"
        )
        if category.id in category_ids
    ]


async def send_menu_view(
    *, target, bot: Bot, category_id: str | None = None, user=None, edit: bool = True
) -> None:
    """The menu for the guest. A callback edits its message unless ``edit`` is
    false; ``user`` is the guest when ``target`` is the bot's own message."""

    user = user or target.from_user
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    navigation_state = await sync_to_async(resolve_guest_navigation_state)(
        partner_id=partner.id,
        telegram_id=user.id,
        content=content,
    )
    main_keyboard = build_main_keyboard(content, navigation_state=navigation_state)
    if not content.supports_menu():
        await _answer(
            target, _("Меню сейчас отключено для этого бота."), reply_markup=main_keyboard
        )
        return

    session = None
    if content.supports_tables():
        session = await sync_to_async(get_active_table_session)(
            partner_id=partner.id,
            telegram_id=user.id,
        )
    if session is None and not content.allow_menu_without_session:
        await _answer(target, content.menu_requires_session_hint(), reply_markup=main_keyboard)
        return

    channel = None
    if content.supports_cart():
        channel = await sync_to_async(ordering_channel_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=user.id,
        )
    categories = await sync_to_async(_categories_with_items)(partner.id, channel)
    if not categories:
        await _answer(target, content.menu_empty_message_template, reply_markup=main_keyboard)
        return

    category = next(
        (item for item in categories if str(item.id) == str(category_id)),
        categories[0],
    )
    menu_items = await sync_to_async(list)(
        MenuItemRepository.active_for_partner(partner.id, channel=channel).filter(
            category=category
        )[: content.max_menu_items_per_category_message]
    )
    cart = None
    if channel is not None:
        cart = await sync_to_async(get_active_cart_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=user.id,
        )
    text = _render_menu_category_view(
        partner=partner,
        content=content,
        category=category,
        menu_items=menu_items,
        session=session,
        channel=channel,
    )
    keyboard = build_menu_keyboard(
        categories=categories,
        active_category_id=category.id,
        items=menu_items,
        has_session=channel is not None,
        supports_cart=content.supports_cart(),
        cart=cart,
        choose_way=[] if channel is not None else choose_way_buttons(content),
    )
    if isinstance(target, CallbackQuery) and edit:
        updated = await _safe_edit_menu_message(
            target.message,
            text,
            reply_markup=keyboard,
        )
        if updated:
            await target.answer()
        else:
            await target.answer(_("Без изменений"))
    elif isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=keyboard)
    else:
        await target.answer(text, reply_markup=keyboard)


@router.message(PartnerButtonFilter("button_menu_label"))
async def menu_handler(message: Message, bot: Bot) -> None:
    await send_menu_view(target=message, bot=bot)


@router.callback_query(F.data.startswith("menucat:"))
async def menu_category_callback(callback: CallbackQuery, bot: Bot) -> None:
    category_id = (callback.data or "").split(":", 1)[1]
    await send_menu_view(target=callback, bot=bot, category_id=category_id)


@router.callback_query(F.data == "menurefresh")
async def menu_refresh_callback(callback: CallbackQuery, bot: Bot) -> None:
    await send_menu_view(target=callback, bot=bot)


@router.callback_query(F.data.startswith("menuadd:"))
async def menu_add_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    if not content.supports_cart():
        await callback.answer(content.cart_disabled_message(), show_alert=True)
        return

    item_public_id = (callback.data or "").split(":", 1)[1]
    try:
        cart = await sync_to_async(add_menu_item_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            item_public_id=item_public_id,
        )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(_("Добавлено в корзину."))
    # The cart button under the menu shows the new count and total.
    try:
        await callback.message.edit_reply_markup(
            reply_markup=refresh_cart_button(callback.message.reply_markup, cart)
        )
    except (TelegramBadRequest, AttributeError):
        pass
