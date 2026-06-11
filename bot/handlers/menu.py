from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.menu.models import MenuCategory
from apps.menu.repositories import MenuItemRepository
from apps.orders.services import (
    OrderFlowError,
    add_items_to_cart_for_telegram_user,
)
from apps.tables.services import get_active_table_session
from bot.filters.content import PartnerButtonFilter
from bot.keyboards.main import build_main_keyboard
from bot.keyboards.menu import build_menu_keyboard
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state

router = Router()


def _render_menu_category_view(*, partner, content, category, menu_items, session) -> str:
    lines = [content.menu_header(partner_name=partner.name)]
    lines.append(f"Категория: <b>{category.name}</b>")
    lines.append("")
    for item in menu_items:
        category_name = item.category.name if item.category_id else "Без категории"
        lines.append(f"{item.name} • {item.price} грн • {category_name}")
        # If category chips become visually enough later, we can trim the
        # repeated category label here without changing add-to-cart behavior.
        if item.description:
            lines.append(item.description)
        lines.append("")

    if session is None:
        # Guests may browse categories and positions freely, but ordering
        # remains locked until a real in-venue table session exists.
        lines.append(content.menu_requires_session_hint())
    else:
        lines.append("Нажмите кнопку с позицией ниже, чтобы добавить её в корзину.")
        lines.extend(content.ordering_entry_lines(table_number=session.table.number))
    return "\n".join(lines).strip()


async def _send_menu_view(*, target, bot: Bot, category_id: str | None = None) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    navigation_state = await sync_to_async(resolve_guest_navigation_state)(
        partner_id=partner.id,
        telegram_id=target.from_user.id,
        content=content,
    )
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=target.from_user.id,
    )
    if session is None and not content.allow_menu_without_session:
        if isinstance(target, CallbackQuery):
            await target.message.answer(
                content.menu_requires_session_hint(),
                reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
            )
            await target.answer()
        else:
            await target.answer(
                content.menu_requires_session_hint(),
                reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
            )
        return

    categories = await sync_to_async(list)(
        MenuCategory.objects.filter(partner_id=partner.id, is_active=True).order_by(
            "sort_order",
            "name",
        )
    )
    if not categories:
        if isinstance(target, CallbackQuery):
            await target.message.answer(
                content.menu_empty_message_template,
                reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
            )
            await target.answer()
        else:
            await target.answer(
                content.menu_empty_message_template,
                reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
            )
        return

    category = next(
        (item for item in categories if str(item.id) == str(category_id)),
        categories[0],
    )
    menu_items = await sync_to_async(list)(
        MenuItemRepository.active_for_partner(partner.id).filter(category=category)[:8]
    )
    text = _render_menu_category_view(
        partner=partner,
        content=content,
        category=category,
        menu_items=menu_items,
        session=session,
    )
    keyboard = build_menu_keyboard(
        categories=categories,
        active_category_id=category.id,
        items=menu_items,
        has_session=session is not None,
    )
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard)


@router.message(Command("menu"))
@router.message(PartnerButtonFilter("button_menu_label"))
async def menu_handler(message: Message, bot: Bot) -> None:
    await _send_menu_view(target=message, bot=bot)


@router.callback_query(F.data.startswith("menucat:"))
async def menu_category_callback(callback: CallbackQuery, bot: Bot) -> None:
    category_id = (callback.data or "").split(":", 1)[1]
    await _send_menu_view(target=callback, bot=bot, category_id=category_id)


@router.callback_query(F.data == "menurefresh")
async def menu_refresh_callback(callback: CallbackQuery, bot: Bot) -> None:
    await _send_menu_view(target=callback, bot=bot)


@router.callback_query(F.data.startswith("menuadd:"))
async def menu_add_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    item_public_id = (callback.data or "").split(":", 1)[1]
    try:
        await sync_to_async(add_items_to_cart_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            raw_items=f"{item_public_id}x1",
        )
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Позиция добавлена в корзину.")
