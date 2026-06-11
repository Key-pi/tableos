from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.notifications.services import GuestCallError, request_staff_assistance
from apps.tables.services import get_active_table_session
from bot.filters.content import PartnerButtonFilter
from bot.keyboards.guest_call import build_guest_call_keyboard
from bot.keyboards.main import build_main_keyboard
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state

router = Router()


async def _resolve_partner_and_content(bot: Bot):
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    return partner, content


@router.message(Command("call_staff"))
@router.message(PartnerButtonFilter("button_call_staff_label"))
async def call_staff_handler(message: Message, bot: Bot) -> None:
    partner, content = await _resolve_partner_and_content(bot)
    navigation_state = await sync_to_async(resolve_guest_navigation_state)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
        content=content,
    )
    if not content.supports_staff_call():
        await message.answer(
            "Вызов персонала сейчас отключён для этого бота.",
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return

    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
    )
    if session is None:
        await message.answer(
            "Позвать персонал можно после активации стола по QR-коду.",
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return

    await message.answer(
        "\n".join(
            [
                content.staff_call_prompt_message(),
                f"Активный стол: #{session.table.number}",
            ]
        ),
        reply_markup=build_guest_call_keyboard(
            targets=content.available_staff_call_targets(),
        ),
    )


@router.callback_query(F.data.startswith("guestcall:request:"))
async def guest_call_request_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner, content = await _resolve_partner_and_content(bot)
    if not content.supports_staff_call():
        await callback.answer("Вызов персонала отключён.", show_alert=True)
        return

    call_target = (callback.data or "").split(":", 2)[2]
    available_targets = dict(content.available_staff_call_targets())
    call_target_label = available_targets.get(call_target)
    if call_target_label is None:
        await callback.answer("Этот вариант вызова сейчас недоступен.", show_alert=True)
        return

    try:
        table_number, queued_count = await sync_to_async(request_staff_assistance)(
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            call_target=call_target,
            call_target_label=call_target_label,
        )
    except GuestCallError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.edit_text(
        "\n".join(
            [
                content.staff_call_success_message(
                    partner_name=partner.name,
                    table_number=table_number,
                    call_target_label=call_target_label,
                ),
                f"Уведомления поставлены в очередь: {queued_count}",
            ]
        )
    )
    await callback.answer("Запрос отправлен.")


@router.callback_query(F.data == "guestcall:cancel")
async def guest_call_cancel_callback(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Вызов персонала отменён.")
    await callback.answer()
