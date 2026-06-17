from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.bonuses.selectors import (
    active_bonus_programs_for_partner,
    recent_bonus_transactions_for_guest,
)
from apps.tables.services import get_active_table_session
from apps.users.services import get_or_create_guest_profile
from bot.filters.content import PartnerButtonFilter
from bot.keyboards.main import build_main_keyboard
from bot.keyboards.profile import (
    build_profile_bonuses_keyboard,
    build_profile_summary_keyboard,
)
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state

router = Router()

_BONUS_TRANSACTIONS_PAGE_SIZE = 5
_BONUS_PROGRAMS_PREVIEW_LIMIT = 4


def _format_dt(value) -> str:
    if not value:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M")


def _render_profile_summary(
    *,
    partner_name: str,
    guest_profile,
    active_session,
) -> str:
    telegram_username = guest_profile.telegram_account.username
    identity_line = f"@{telegram_username}" if telegram_username else str(
        guest_profile.telegram_account.telegram_id
    )
    lines = [
        "<b>Мой профиль</b>",
        f"Заведение: {partner_name}",
        f"Telegram: {identity_line}",
        f"Код клиента: <code>{guest_profile.customer_code}</code>",
        f"Баланс: {guest_profile.loyalty_balance} бонусов",
        "",
        "<b>Статус</b>",
        (
            f"Активный стол: #{active_session.table.number}"
            if active_session is not None
            else "Активного стола сейчас нет."
        ),
        f"Первый визит: {_format_dt(guest_profile.first_visit_at)}",
        f"Последний визит: {_format_dt(guest_profile.last_visit_at)}",
        "",
        "Код клиента можно назвать на баре или кассе для бонусов и быстрых продаж.",
    ]
    return "\n".join(lines)


def _render_profile_bonuses(
    *,
    guest_profile,
    active_programs,
    recent_transactions,
    page: int,
) -> tuple[str, int]:
    page = max(page, 0)
    total_transactions = len(recent_transactions)
    total_pages = max(1, (total_transactions + _BONUS_TRANSACTIONS_PAGE_SIZE - 1) // _BONUS_TRANSACTIONS_PAGE_SIZE)
    page = min(page, total_pages - 1)
    start = page * _BONUS_TRANSACTIONS_PAGE_SIZE
    end = start + _BONUS_TRANSACTIONS_PAGE_SIZE
    page_transactions = recent_transactions[start:end]

    lines = [
        "<b>Бонусы</b>",
        f"Текущий баланс: {guest_profile.loyalty_balance} бонусов",
        "",
        "<b>Активные программы</b>",
    ]
    if active_programs:
        preview_programs = active_programs[:_BONUS_PROGRAMS_PREVIEW_LIMIT]
        for program in preview_programs:
            lines.append(
                f"• {program.name} — {program.get_program_type_display()} / "
                f"{program.get_trigger_event_display()}"
            )
        hidden_programs = len(active_programs) - len(preview_programs)
        if hidden_programs > 0:
            lines.append(f"• И ещё программ: {hidden_programs}")
    else:
        lines.append("• Сейчас активных бонусных программ нет.")

    lines.extend(["", "<b>Последние движения</b>"])
    if page_transactions:
        for transaction in page_transactions:
            program_name = transaction.program.name if transaction.program_id else "Ручная операция"
            sign = "+" if transaction.amount >= 0 else ""
            lines.append(
                f"• {sign}{transaction.amount} через {program_name} "
                f"({transaction.created_at:%d.%m %H:%M})"
            )
    else:
        lines.append("• История бонусов пока пуста.")
    if total_pages > 1:
        lines.append("")
        lines.append(f"Страница истории: {page + 1}/{total_pages}")
    return "\n".join(lines), total_pages


async def _safe_profile_message_edit(message: Message, text: str, *, reply_markup) -> bool:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return False
        raise


@router.message(PartnerButtonFilter("button_my_profile"))
async def profile_handler(message: Message, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    guest_profile = await sync_to_async(get_or_create_guest_profile)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
        language_code=message.from_user.language_code or "",
    )
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
    )
    await message.answer(
        _render_profile_summary(
            partner_name=partner.name,
            guest_profile=guest_profile,
            active_session=session,
        ),
        reply_markup=build_profile_summary_keyboard(
            bonuses_label=content.button_profile_bonuses_label,
        ),
    )


@router.callback_query(F.data == "guestprofile:summary")
async def profile_summary_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    guest_profile = await sync_to_async(get_or_create_guest_profile)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username or "",
        first_name=callback.from_user.first_name or "",
        last_name=callback.from_user.last_name or "",
        language_code=callback.from_user.language_code or "",
    )
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
    )
    updated = await _safe_profile_message_edit(
        callback.message,
        _render_profile_summary(
            partner_name=partner.name,
            guest_profile=guest_profile,
            active_session=session,
        ),
        reply_markup=build_profile_summary_keyboard(
            bonuses_label=content.button_profile_bonuses_label,
        ),
    )
    if updated:
        await callback.answer()
    else:
        await callback.answer("Без изменений")


@router.callback_query(F.data.startswith("guestprofile:bonuses:"))
async def profile_bonuses_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    guest_profile = await sync_to_async(get_or_create_guest_profile)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username or "",
        first_name=callback.from_user.first_name or "",
        last_name=callback.from_user.last_name or "",
        language_code=callback.from_user.language_code or "",
    )
    try:
        page = int((callback.data or "").rsplit(":", 1)[1])
    except (IndexError, ValueError):
        page = 0
    active_programs = await sync_to_async(list)(active_bonus_programs_for_partner(partner.id))
    recent_transactions = await sync_to_async(list)(
        recent_bonus_transactions_for_guest(guest_profile.id, limit=25)
    )
    text, total_pages = _render_profile_bonuses(
        guest_profile=guest_profile,
        active_programs=active_programs,
        recent_transactions=recent_transactions,
        page=page,
    )
    normalized_page = min(max(page, 0), total_pages - 1)
    updated = await _safe_profile_message_edit(
        callback.message,
        text,
        reply_markup=build_profile_bonuses_keyboard(
            page=normalized_page,
            total_pages=total_pages,
        ),
    )
    if updated:
        await callback.answer()
    else:
        await callback.answer("Без изменений")
