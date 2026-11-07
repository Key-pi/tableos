from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, WebAppInfo
from asgiref.sync import sync_to_async

from apps.menu.catalog import (
    badges_of,
    catalog_tree,
    closed_category,
    has_old_price,
    portion_label,
    schedule_label,
    showcase_on,
)
from apps.menu.options import catalog_choices, choices_for, line_name
from apps.menu.repositories import MenuItemRepository
from apps.menu.stock import counted, item_state, stock_on
from apps.menu.translations import category_name, translated
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
from bot.keyboards.options import (
    NOOP,
    build_choice_keyboard,
    chosen,
    decode,
    over_limit,
    starting_pick,
    valid,
)
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.mini_app import mini_app_link_for
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


# Stock (catalog step 3, owner 2026-11-07): never the number.
STOCK_SUFFIX = {
    "out": N_(" — нет в наличии"),
    "low": N_(" · осталось мало"),
}


def _item_line(item, *, showcase: bool, choices=None, stock: str = "") -> str:
    line = _price_line(item, showcase=showcase, choices=choices)
    if stock in STOCK_SUFFIX:
        line += _(STOCK_SUFFIX[stock])
    return line


def _price_line(item, *, showcase: bool, choices=None) -> str:
    variants = choices.variants if choices is not None else []
    if variants:
        # Variants with their own price (catalog step 2): from the lowest.
        line = _(
            "{name} — от {price} грн", name=escape_html(translated(item, "name")),
            price=min(variant.price for variant in variants),
        )
    else:
        line = _(
            "{name} — {price} грн", name=escape_html(translated(item, "name")), price=item.price
        )
    if not showcase:
        return line
    # The showcase module (owner, 2026-11-07): labels, old price, portion.
    emojis = "".join(badge["emoji"] for badge in badges_of(item) if badge["emoji"])
    if emojis:
        line = f"{emojis} {line}"
    if has_old_price(item) and not variants:
        line += f" <s>{escape_html(_('{price} грн', price=item.old_price))}</s>"
    portion = portion_label(item)
    if portion:
        line += f" · {escape_html(portion)}"
    return line


def _render_menu_category_view(
    *, partner, content, category, menu_items, session, channel=None, closed_when: str = "",
    showcase: bool = False, choices=None, stock=None,
) -> str:
    lines = [content.menu_header(partner_name=partner.name)]
    # The catalog in the guest's language (catalog step 4).
    lines.append(_("Категория: <b>{name}</b>", name=escape_html(category_name(category))))
    if closed_when:
        # Shown all day, sold only in its time (owner, 2026-11-07).
        lines.append(
            _("🕐 Продаётся {when} — сейчас заказать нельзя.", when=escape_html(closed_when))
        )
    lines.append("")
    if not menu_items:
        lines.append(_("В этой категории сейчас нет доступных позиций."))
        lines.append("")
    else:
        for item in menu_items:
            item_choices = choices.get(item.id) if choices else None
            lines.append(_item_line(
                item, showcase=showcase, choices=item_choices,
                stock=stock.get(item.id, "") if stock else "",
            ))
            description = translated(item, "description")
            if description:
                lines.append(f"<i>{escape_html(_trim_menu_description(description))}</i>")
            lines.append("")
    lines.extend(_ordering_hint(content, channel=channel, session=session))
    return "\n".join(lines).strip()


async def _answer(target, text: str, *, reply_markup) -> None:
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=reply_markup)
        await target.answer()
    else:
        await target.answer(text, reply_markup=reply_markup)


def _menu_tree(partner, channel, showcase: bool):
    """Top categories with their items and subcategories (two levels) that
    have something to show in this way of ordering."""

    items = MenuItemRepository.active_for_partner(partner.id, channel=channel).select_related(
        "category__parent"
    )
    if showcase:
        items = items.prefetch_related("custom_badges")
    return catalog_tree(partner.id, list(items))


def _stock_states(partner, items, choices) -> dict:
    """{item id: "out" | "low" | ""} while the stock module is on."""

    if not stock_on(partner.id):
        return {}
    return {
        item.id: item_state(item, choices[item.id].variants, on=True)
        for item in items if item.track_stock
    }


def _pick(tree, category_id):
    """(top branch, chosen entry): the asked category, else the first top;
    a top with no own items opens its first subcategory."""

    for branch in tree:
        if str(branch["category"].id) == str(category_id):
            break
        for child in branch["children"]:
            if str(child["category"].id) == str(category_id):
                return branch, child
    else:
        branch = tree[0]
    if not branch["items"] and branch["children"]:
        return branch, branch["children"][0]
    return branch, branch


MINI_APP_BUTTON = N_("📱 Открыть меню в приложении")


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
    showcase = await sync_to_async(showcase_on)(partner)
    tree = await sync_to_async(_menu_tree)(partner, channel, showcase)
    if not tree:
        await _answer(target, content.menu_empty_message_template, reply_markup=main_keyboard)
        return

    branch, chosen = _pick(tree, category_id)
    category = chosen["category"]
    menu_items = chosen["items"][: content.max_menu_items_per_category_message]
    closed = await sync_to_async(closed_category)(category)
    closed_when = schedule_label(closed) if closed is not None else ""
    choices = await sync_to_async(catalog_choices)(partner, menu_items)
    stock = await sync_to_async(_stock_states)(partner, menu_items, choices)
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
        closed_when=closed_when,
        showcase=showcase,
        choices=choices,
        stock=stock,
    )
    keyboard = build_menu_keyboard(
        categories=[entry["category"] for entry in tree],
        active_category_id=branch["category"].id,
        subcategories=[entry["category"] for entry in branch["children"]],
        active_subcategory_id=category.id if category.parent_id else None,
        items=[] if closed is not None else menu_items,
        has_session=channel is not None,
        supports_cart=content.supports_cart(),
        cart=cart,
        choose_way=[] if channel is not None else choose_way_buttons(content),
        choices=choices,
        sold_out={item_id for item_id, state in stock.items() if state == "out"},
    )
    link = await sync_to_async(mini_app_link_for)(bot.token, partner.id)
    if link:
        # PQ-048: the same menu with photos in the Mini App; the bot stays.
        keyboard.inline_keyboard.insert(
            0, [InlineKeyboardButton(text=_(MINI_APP_BUTTON), web_app=WebAppInfo(url=link))]
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


# --- a variant and add-ons (catalog step 2) ---------------------------------


def _choice_item(partner, public_id: str):
    """The item on sale with what can be chosen for it, or (None, None)."""

    item = (
        MenuItemRepository.active_for_partner(partner.id)
        .select_related("category__parent")
        .filter(public_id=public_id)
        .first()
    )
    if item is None:
        return None, None
    return item, choices_for(item)


def _render_choice_view(item, choices, pick) -> str:
    variant, modifiers = chosen(choices, pick)
    lines = [f"<b>{escape_html(translated(item, 'name'))}</b>"]
    description = translated(item, "description")
    if description:
        lines.append(f"<i>{escape_html(_trim_menu_description(description))}</i>")
    lines.append("")
    lines.append(_("Выберите вариант и добавки — сумма на кнопке «Добавить»."))
    current = line_name(
        translated(item, "name"),
        translated(variant, "name") if variant is not None else "",
        [translated(modifier, "name") for modifier in modifiers],
    )
    lines.append(_("Сейчас: {name}", name=escape_html(current)))
    return "\n".join(lines)


async def _choice_context(callback: CallbackQuery, bot: Bot):
    """(partner, item, choices, pick) for a choice button, or None when the
    guest has been answered already."""

    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    if not content.supports_cart():
        await callback.answer(content.cart_disabled_message(), show_alert=True)
        return None
    try:
        public_id, pick = decode(callback.data or "")
    except ValueError:
        await callback.answer()
        return None
    item, choices = await sync_to_async(_choice_item)(partner, public_id)
    if item is None:
        await callback.answer(_("Эта позиция сейчас недоступна."), show_alert=True)
        return None
    stock = await sync_to_async(counted)(item)
    return partner, item, choices, pick, stock


@router.callback_query(F.data.startswith("mo:"))
async def menu_choice_callback(callback: CallbackQuery, bot: Bot) -> None:
    """Opens or redraws the choice of an item; the choice is in the data."""

    if callback.data == NOOP:
        await callback.answer()
        return
    context = await _choice_context(callback, bot)
    if context is None:
        return
    partner, item, choices, pick, stock = context
    if pick is None or not valid(choices, pick):
        pick = starting_pick(choices, stock=stock)
    group = over_limit(choices, pick)
    if group is not None:
        await callback.answer(
            _("«{group}»: не больше {count}.", group=translated(group, "name"),
              count=group.max_choices),
            show_alert=True,
        )
        return
    cart = await sync_to_async(get_active_cart_for_telegram_user)(
        partner_id=partner.id, telegram_id=callback.from_user.id
    )
    keyboard = build_choice_keyboard(
        item, choices, pick, back_callback=f"menucat:{item.category_id}", cart=cart, stock=stock
    )
    await _safe_edit_menu_message(
        callback.message, _render_choice_view(item, choices, pick), reply_markup=keyboard
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ma:"))
async def menu_choice_add_callback(callback: CallbackQuery, bot: Bot) -> None:
    """Adds the chosen variant and add-ons; the cart service checks them."""

    context = await _choice_context(callback, bot)
    if context is None:
        return
    partner, item, choices, pick, stock = context
    if pick is None or not valid(choices, pick):
        await callback.answer(_("Меню изменилось — выберите заново."), show_alert=True)
        return
    variant, modifiers = chosen(choices, pick)
    try:
        cart = await sync_to_async(add_menu_item_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            item_public_id=item.public_id,
            variant_id=variant.id.hex if variant is not None else None,
            modifier_ids=[modifier.id.hex for modifier in modifiers],
        )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(_("Добавлено в корзину."))
    keyboard = build_choice_keyboard(
        item, choices, pick, back_callback=f"menucat:{item.category_id}", cart=cart, stock=stock
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except (TelegramBadRequest, AttributeError):
        pass
