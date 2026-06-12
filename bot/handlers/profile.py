from aiogram import Bot, Router
from aiogram.types import Message
from asgiref.sync import sync_to_async

from apps.bonuses.selectors import (
    active_bonus_programs_for_partner,
    recent_bonus_transactions_for_guest,
)
from apps.tables.services import get_active_table_session
from apps.users.services import get_or_create_guest_profile
from bot.filters.content import PartnerButtonFilter
from bot.keyboards.main import build_main_keyboard
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state

router = Router()


def _format_dt(value) -> str:
    if not value:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M")


def _render_profile_summary(
    *,
    partner_name: str,
    guest_profile,
    active_programs,
    recent_transactions,
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
        "<b>Активные программы</b>",
    ]
    if active_programs:
        for program in active_programs:
            lines.append(
                f"• {program.name} — {program.get_program_type_display()} / "
                f"{program.get_trigger_event_display()}"
            )
    else:
        lines.append("• Сейчас активных бонусных программ нет.")

    lines.append("")
    lines.append("<b>Последние движения по бонусам</b>")
    if recent_transactions:
        for transaction in recent_transactions:
            program_name = transaction.program.name if transaction.program_id else "Manual"
            sign = "+" if transaction.amount >= 0 else ""
            lines.append(
                f"• {sign}{transaction.amount} через {program_name} "
                f"({transaction.created_at:%d.%m %H:%M})"
            )
    else:
        lines.append("• История бонусов пока пуста.")
    lines.extend(
        [
            "",
            "Этот код можно назвать на баре или кассе, чтобы получить бонусы за быструю продажу.",
        ]
    )
    return "\n".join(lines)


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
    active_programs = await sync_to_async(list)(active_bonus_programs_for_partner(partner.id))
    recent_transactions = await sync_to_async(list)(
        recent_bonus_transactions_for_guest(guest_profile.id, limit=5)
    )
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
    )
    await message.answer(
        _render_profile_summary(
            partner_name=partner.name,
            guest_profile=guest_profile,
            active_programs=active_programs,
            recent_transactions=recent_transactions,
            active_session=session,
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
