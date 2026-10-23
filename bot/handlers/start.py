from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.analytics.sources import parse_source_payload, record_source_visit
from apps.employees.models import EmployeeProfile
from apps.employees.selectors import get_staff_employee_by_telegram
from apps.orders.models import Order
from apps.orders.services import OrderFlowError, switch_cart_channel_for_telegram_user
from apps.tables.models import Table, TableVisit
from apps.tables.services import (
    VISIT_CHOICE_ASK,
    VISIT_CHOICE_JOIN_PREFIX,
    VISIT_CHOICE_NEW,
    TableSessionError,
    VisitChoiceRequired,
    activate_table_session,
)
from apps.users.services import get_or_create_guest_profile_with_created
from bot.keyboards.main import build_main_keyboard
from bot.keyboards.session import build_visit_choice_keyboard
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state
from bot.utils.html import escape_html

router = Router()


@router.message(CommandStart())
async def start_handler(message: Message, command: CommandObject, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    guest_profile, is_new_guest = await sync_to_async(get_or_create_guest_profile_with_created)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
        language_code=message.from_user.language_code or "",
    )
    payload = (command.args or "").strip()
    if parse_source_payload(payload) is not None:
        # A traffic-source link (`src_<code>`): count it and greet as usual.
        await sync_to_async(record_source_visit)(
            partner_id=partner.id,
            guest_id=guest_profile.id,
            payload=payload,
            is_new_guest=is_new_guest,
        )
        payload = ""
    try:
        employee = await sync_to_async(get_staff_employee_by_telegram)(
            partner.id,
            message.from_user.id,
        )
    except EmployeeProfile.DoesNotExist:
        employee = None

    if payload:
        if not content.supports_tables():
            await message.answer(
                "Столы сейчас отключены для этого бота.",
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

        await _activate_and_reply(
            message=message,
            user=message.from_user,
            partner=partner,
            content=content,
            payload=payload,
            visit_choice=VISIT_CHOICE_ASK,
        )
        return

    start_hint_lines = content.start_hint_lines()
    await message.answer(
        "\n".join(
            [
                content.welcome_message(partner_name=partner.name),
                "",
                f"Вы зарегистрированы в программе лояльности {escape_html(partner.name)}.",
                f"Ваш код клиента: <code>{guest_profile.customer_code}</code>",
                "Этот код можно назвать на баре или кассе для начисления бонусов.",
            ]
            + ([""] + start_hint_lines if start_hint_lines else [])
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


async def _activate_and_reply(*, message: Message, user, partner, content, payload, visit_choice):
    try:
        session = await sync_to_async(activate_table_session)(
            partner_id=partner.id,
            telegram_id=user.id,
            payload=payload,
            username=user.username or "",
            first_name=user.first_name or "",
            last_name=user.last_name or "",
            language_code=user.language_code or "",
            visit_choice=visit_choice,
        )
    except VisitChoiceRequired as exc:
        await message.answer(
            "За этим столом уже сидит компания. Подсесть к ней или начать новый визит?",
            reply_markup=build_visit_choice_keyboard(
                table_id=exc.table.id,
                options=[
                    (option.visit_id, _visit_option_label(option, partner.timezone))
                    for option in exc.options
                ],
            ),
        )
        return
    except TableSessionError as exc:
        await message.answer(escape_html(str(exc)))
        return

    guest_profile = session.guest
    removed: list[str] = []
    if content.supports_cart():
        # Scanning a table QR is choosing the table flow.
        try:
            _cart, removed = await sync_to_async(switch_cart_channel_for_telegram_user)(
                partner_id=partner.id,
                telegram_id=user.id,
                channel=Order.Channel.TABLE,
            )
        except OrderFlowError:
            removed = []
    await message.answer(
        "\n".join(
            [
                content.table_activated_message(
                    partner_name=partner.name,
                    table_number=session.table.number,
                ),
                "",
                f"Ваш код клиента: <code>{guest_profile.customer_code}</code>",
            ]
            + (
                [
                    "",
                    "Убрали из корзины — не подаётся за столом: "
                    + escape_html(", ".join(removed)),
                ]
                if removed
                else []
            )
        ),
        reply_markup=build_main_keyboard(
            content,
            navigation_state=await sync_to_async(resolve_guest_navigation_state)(
                partner_id=partner.id,
                telegram_id=user.id,
                content=content,
            ),
        ),
    )


def _visit_option_label(option, partner_timezone: str) -> str:
    # No guest names: other guests' identities are not shown (ADR-0007).
    started = option.started_at.astimezone(ZoneInfo(partner_timezone)).strftime("%H:%M")
    return f"Подсесть к компании (с {started}, гостей: {option.guest_count})"


def _resolve_visit_choice_target(*, partner_id, action: str, raw_id: str):
    """Return (table payload, visit_choice) for a join/new callback, or None."""

    try:
        object_id = UUID(hex=raw_id)
    except ValueError:
        return None
    if action == "j":
        visit = (
            TableVisit.objects.select_related("table")
            .filter(partner_id=partner_id, id=object_id, table__is_active=True)
            .first()
        )
        if visit is None:
            return None
        return visit.table.deep_link_payload, f"{VISIT_CHOICE_JOIN_PREFIX}{visit.id}"
    if action == "n":
        table = Table.objects.filter(partner_id=partner_id, id=object_id, is_active=True).first()
        if table is None:
            return None
        return table.deep_link_payload, VISIT_CHOICE_NEW
    return None


@router.callback_query(F.data.startswith("tv:"))
async def visit_choice_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    parts = (callback.data or "").split(":")
    target = None
    if len(parts) == 3 and content.supports_tables():
        target = await sync_to_async(_resolve_visit_choice_target)(
            partner_id=partner.id,
            action=parts[1],
            raw_id=parts[2],
        )
    if target is None:
        await callback.answer("Выбор устарел. Отсканируйте QR-код стола ещё раз.", show_alert=True)
        return

    payload, visit_choice = target
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 - the old prompt may already be gone
        pass
    await _activate_and_reply(
        message=callback.message,
        user=callback.from_user,
        partner=partner,
        content=content,
        payload=payload,
        visit_choice=visit_choice,
    )
