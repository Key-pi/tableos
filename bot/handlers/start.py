from aiogram import Bot, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from asgiref.sync import sync_to_async

from apps.employees.models import EmployeeProfile
from apps.employees.selectors import get_staff_employee_by_telegram
from apps.tables.services import TableSessionError, activate_table_session
from apps.users.services import get_or_create_guest_profile
from bot.keyboards.main import build_main_keyboard
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state

router = Router()


@router.message(CommandStart())
async def start_handler(message: Message, command: CommandObject, bot: Bot) -> None:
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
    payload = (command.args or "").strip()
    try:
        employee = await sync_to_async(get_staff_employee_by_telegram)(
            partner.id,
            message.from_user.id,
        )
    except EmployeeProfile.DoesNotExist:
        employee = None

    if payload:
        try:
            session = await sync_to_async(activate_table_session)(
                partner_id=partner.id,
                telegram_id=message.from_user.id,
                payload=payload,
                username=message.from_user.username or "",
                first_name=message.from_user.first_name or "",
                last_name=message.from_user.last_name or "",
                language_code=message.from_user.language_code or "",
            )
        except TableSessionError as exc:
            await message.answer(str(exc))
            return

        await message.answer(
            "\n".join(
                [
                    content.table_activated_message(
                        partner_name=partner.name,
                        table_number=session.table.number,
                    ),
                    "",
                    f"Ваш бонусный код: <code>{guest_profile.customer_code}</code>",
                ]
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
        return

    await message.answer(
        "\n".join(
            [
                content.welcome_message(partner_name=partner.name),
                "",
                f"Вы зарегистрированы в программе лояльности {partner.name}.",
                f"Ваш код клиента: <code>{guest_profile.customer_code}</code>",
                "Этот код можно назвать на баре или кассе для начисления бонусов.",
            ]
            + (
                [
                    "",
                    "Если вы сотрудник заведения, откройте рабочий режим командой /staff.",
                ]
                if employee is not None
                else []
            )
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
