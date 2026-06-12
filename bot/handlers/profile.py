from math import ceil

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.bonuses.repositories import BonusTransactionRepository
from apps.bonuses.selectors import (
    active_bonus_programs_for_partner,
)
from apps.tables.services import get_active_table_session
from apps.users.services import get_or_create_guest_profile
from bot.filters.content import PartnerButtonFilter
from bot.keyboards.profile import (
    build_profile_bonus_keyboard,
    build_profile_overview_keyboard,
)
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token

router = Router()
BONUS_TRANSACTIONS_PAGE_SIZE = 5
MAX_VISIBLE_BONUS_PROGRAMS = 4


def _format_dt(value) -> str:
    if not value:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M")


def _render_profile_summary(
    *,
    partner_name: str,
    guest_profile,
    active_session,
    bonuses_button_label: str,
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
    ]
    lines.extend(
        [
            "",
            f"Кнопкой «{bonuses_button_label}» ниже можно открыть программы лояльности "
            "и историю операций.",
        ]
    )
    return "\n".join(lines)


def _render_bonus_programs(active_programs) -> list[str]:
    if not active_programs:
        return ["• Сейчас активных бонусных программ нет."]

    lines = [
        f"• {program.name} — {program.get_program_type_display()} / "
        f"{program.get_trigger_event_display()}"
        for program in active_programs[:MAX_VISIBLE_BONUS_PROGRAMS]
    ]
    hidden_programs = len(active_programs) - MAX_VISIBLE_BONUS_PROGRAMS
    if hidden_programs > 0:
        lines.append(f"• И ещё {hidden_programs} программ(ы).")
    return lines


def _render_bonus_transactions(*, transactions, page: int, total_count: int) -> list[str]:
    if not transactions:
        return ["• История бонусов пока пуста."]

    start_index = (page - 1) * BONUS_TRANSACTIONS_PAGE_SIZE + 1
    end_index = start_index + len(transactions) - 1
    lines = [f"Показаны операции {start_index}-{end_index} из {total_count}."]
    for transaction in transactions:
        program_name = transaction.program.name if transaction.program_id else "Ручная операция"
        sign = "+" if transaction.amount >= 0 else ""
        lines.append(
            f"• {sign}{transaction.amount} • {program_name} • "
            f"{transaction.get_transaction_type_display()} • "
            f"{transaction.created_at:%d.%m %H:%M}"
        )
    return lines


def _render_bonus_summary(
    *,
    partner_name: str,
    guest_profile,
    active_programs,
    transactions,
    page: int,
    total_pages: int,
    total_transactions: int,
) -> str:
    lines = [
        "<b>Бонусы</b>",
        f"Заведение: {partner_name}",
        f"Код клиента: <code>{guest_profile.customer_code}</code>",
        f"Текущий баланс: {guest_profile.loyalty_balance} бонусов",
        "",
        "<b>Активные программы</b>",
    ]
    lines.extend(_render_bonus_programs(active_programs))
    lines.extend(
        [
            "",
            f"<b>История операций</b> • страница {page}/{total_pages}",
        ]
    )
    lines.extend(
        _render_bonus_transactions(
            transactions=transactions,
            page=page,
            total_count=total_transactions,
        )
    )
    lines.extend(
        [
            "",
            "Этот код можно назвать на баре или кассе, чтобы получить бонусы за быструю продажу.",
        ]
    )
    return "\n".join(lines)


async def _resolve_profile_context(*, bot: Bot, telegram_id: int, telegram_user) -> tuple:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    guest_profile = await sync_to_async(get_or_create_guest_profile)(
        partner_id=partner.id,
        telegram_id=telegram_id,
        username=telegram_user.username or "",
        first_name=telegram_user.first_name or "",
        last_name=telegram_user.last_name or "",
        language_code=telegram_user.language_code or "",
    )
    return partner, content, guest_profile


async def _send_profile_overview(*, message: Message, bot: Bot) -> None:
    partner, content, guest_profile = await _resolve_profile_context(
        bot=bot,
        telegram_id=message.from_user.id,
        telegram_user=message.from_user,
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
            bonuses_button_label=content.button_profile_bonuses_label,
        ),
        reply_markup=build_profile_overview_keyboard(
            bonuses_button_label=content.button_profile_bonuses_label,
        ),
    )


async def _show_bonus_page(*, callback: CallbackQuery, bot: Bot, page: int) -> None:
    partner, _content, guest_profile = await _resolve_profile_context(
        bot=bot,
        telegram_id=callback.from_user.id,
        telegram_user=callback.from_user,
    )
    active_programs = await sync_to_async(list)(active_bonus_programs_for_partner(partner.id))
    total_transactions = await sync_to_async(
        lambda: BonusTransactionRepository.for_guest(guest_profile.id).count()
    )()
    total_pages = max(ceil(total_transactions / BONUS_TRANSACTIONS_PAGE_SIZE), 1)
    page = min(max(page, 1), total_pages)
    offset = (page - 1) * BONUS_TRANSACTIONS_PAGE_SIZE
    transactions = await sync_to_async(
        lambda: list(
            BonusTransactionRepository.for_guest(guest_profile.id)[
                offset : offset + BONUS_TRANSACTIONS_PAGE_SIZE
            ]
        )
    )()
    await callback.message.edit_text(
        _render_bonus_summary(
            partner_name=partner.name,
            guest_profile=guest_profile,
            active_programs=active_programs,
            transactions=transactions,
            page=page,
            total_pages=total_pages,
            total_transactions=total_transactions,
        ),
        reply_markup=build_profile_bonus_keyboard(
            page=page,
            total_pages=total_pages,
        ),
    )


@router.message(PartnerButtonFilter("button_my_profile"))
async def profile_handler(message: Message, bot: Bot) -> None:
    await _send_profile_overview(message=message, bot=bot)


@router.callback_query(F.data == "profile:overview")
async def profile_overview_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner, content, guest_profile = await _resolve_profile_context(
        bot=bot,
        telegram_id=callback.from_user.id,
        telegram_user=callback.from_user,
    )
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
    )
    await callback.message.edit_text(
        _render_profile_summary(
            partner_name=partner.name,
            guest_profile=guest_profile,
            active_session=session,
            bonuses_button_label=content.button_profile_bonuses_label,
        ),
        reply_markup=build_profile_overview_keyboard(
            bonuses_button_label=content.button_profile_bonuses_label,
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "profile:noop")
async def profile_noop_callback(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("profile:bonuses:"))
async def profile_bonuses_callback(callback: CallbackQuery, bot: Bot) -> None:
    page_text = (callback.data or "").rsplit(":", 1)[-1]
    try:
        page = int(page_text)
    except ValueError:
        page = 1
    await _show_bonus_page(callback=callback, bot=bot, page=page)
    await callback.answer()
