"""Staff invite links in the bot (PQ-046).

The owner or a manager: «Рабочий режим» → «👥 Пригласить сотрудника» → role
→ a one-time link. The person presses it (/start inv_…); the issuer gets
«Подтвердить / Отклонить». Rules live in ``apps/employees/invites.py``.

Callbacks: ``staffinv`` (roles), ``staffinv:r:<role>`` (issue a link),
``staffinv:y:<invite hex>`` / ``staffinv:n:<invite hex>`` (decide).
"""

from __future__ import annotations

import logging
from contextlib import nullcontext
from urllib.parse import quote
from uuid import UUID

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from asgiref.sync import sync_to_async

from apps.employees.invites import (
    StaffInviteError,
    claim_staff_invite,
    create_staff_invite,
    decide_staff_invite,
    invite_link,
    roles_invitable_by,
)
from apps.employees.models import EmployeeProfile, StaffInvite
from apps.orders.services import OrderFlowError
from apps.partners.languages import speaking_to_employee, speaking_to_guest
from apps.users.models import GuestProfile, User
from bot.handlers.staff import _resolve_staff_employee_from_callback, _safe_edit_message_text
from bot.keyboards.main import build_main_keyboard
from bot.keyboards.staff import back_row
from bot.services.content import BotContent
from bot.services.navigation import resolve_guest_navigation_state
from bot.services.runtime import get_runtime_config_by_token
from bot.utils.html import escape_html
from core.i18n import _

logger = logging.getLogger(__name__)
router = Router(name="staff_invites")

INVITE_CALLBACK = "staffinv"


def _role_label(role: str) -> str:
    return str(User.Role(role).label)


def _person_label(account) -> str:
    name = " ".join(part for part in (account.first_name, account.last_name) if part)
    name = name or str(account.telegram_id)
    if account.username:
        name = f"{name} @{account.username}"
    return name


def _home_row():
    return back_row(_("← Рабочий режим"), "staffhome")


# --- issuing ----------------------------------------------------------------


@router.callback_query(lambda c: c.data == INVITE_CALLBACK)
async def invite_roles_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    roles = await sync_to_async(roles_invitable_by)(employee.user, partner.id)
    if not roles:
        await callback.answer(
            _("Приглашать сотрудников может владелец или менеджер."), show_alert=True
        )
        return
    rows = [
        [InlineKeyboardButton(text=_role_label(role), callback_data=f"{INVITE_CALLBACK}:r:{role}")]
        for role in roles
    ]
    rows.append(_home_row())
    await _safe_edit_message_text(
        callback.message,
        _(
            "<b>Пригласить сотрудника</b>\n"
            "С какой ролью? Ссылка будет одноразовой и проработает 24 часа."
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(lambda c: (c.data or "").startswith(f"{INVITE_CALLBACK}:r:"))
async def invite_create_handler(callback: CallbackQuery, bot: Bot) -> None:
    role = callback.data.split(":", 2)[2]
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        invite = await sync_to_async(create_staff_invite)(
            partner_id=partner.id, actor=employee.user, role=role
        )
    except (OrderFlowError, StaffInviteError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    link = invite_link(invite.code, get_runtime_config_by_token(bot.token).username)
    share_text = _("Приглашение в команду «{partner_name}»", partner_name=partner.name)
    share_url = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(share_text)}"
    await _safe_edit_message_text(
        callback.message,
        _(
            "<b>Приглашение: {role}</b>\n"
            "Отправьте эту ссылку сотруднику:\n{link}\n\n"
            "Ссылка одноразовая и действует 24 часа. Когда сотрудник нажмёт «Start», "
            "я пришлю вам кнопки «Подтвердить» и «Отклонить».",
            role=escape_html(_role_label(invite.role)),
            link=escape_html(link),
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=_("📤 Переслать ссылку"), url=share_url)],
                _home_row(),
            ]
        ),
    )
    await callback.answer()


# --- the person presses the link ---------------------------------------------


def _issuer_prompt(invite_id) -> tuple[int, str, InlineKeyboardMarkup] | None:
    """The message asking the issuer to decide, in the issuer's language."""

    invite = StaffInvite.objects.select_related("claimed_by").get(id=invite_id)
    issuer = (
        EmployeeProfile.objects.select_related("user", "telegram_account")
        .filter(user_id=invite.created_by_id, partner_id=invite.partner_id, is_active=True)
        .first()
    )
    if issuer is None or issuer.telegram_account is None:
        return None  # decided in Admin → Приглашения сотрудников
    with speaking_to_employee(issuer):
        text = _(
            "👥 <b>Новый сотрудник</b>\n{person} хочет в команду: {role}.\n"
            "Подтвердить? Без подтверждения рабочего режима не будет.",
            person=escape_html(_person_label(invite.claimed_by)),
            role=escape_html(_role_label(invite.role)),
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=_("✅ Подтвердить"),
                        callback_data=f"{INVITE_CALLBACK}:y:{invite.id.hex}",
                    ),
                    InlineKeyboardButton(
                        text=_("✖ Отклонить"),
                        callback_data=f"{INVITE_CALLBACK}:n:{invite.id.hex}",
                    ),
                ]
            ]
        )
    return issuer.telegram_account.telegram_id, text, markup


async def _send(bot: Bot, chat_id: int, text: str, markup=None) -> None:
    # The decision is already saved: a blocked chat must not undo it.
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
    except TelegramAPIError as exc:
        logger.warning("Staff invite message to %s not sent: %s", chat_id, exc)


async def handle_invite_start(message: Message, bot: Bot, partner, code: str) -> None:
    """``/start inv_<code>`` from ``bot/handlers/start.py``."""

    try:
        result = await sync_to_async(claim_staff_invite)(
            partner_id=partner.id, code=code, telegram_id=message.from_user.id
        )
    except StaffInviteError as exc:
        await message.answer(escape_html(str(exc)))
        return
    await message.answer(
        _(
            "Заявка отправлена: {role} в «{partner_name}». Как только её подтвердят, "
            "я напишу здесь, и внизу появится кнопка рабочего режима.",
            role=escape_html(_role_label(result.invite.role)),
            partner_name=escape_html(partner.name),
        )
    )
    if result.already_claimed:
        return
    prompt = await sync_to_async(_issuer_prompt)(result.invite.id)
    if prompt is not None:
        await _send(bot, *prompt)


# --- the decision ------------------------------------------------------------


def _answer_to_person(invite_id, partner) -> tuple[int, str, object]:
    """What the person is told, in their language, with the keyboard that
    now has (or still lacks) the work-mode button."""

    invite = StaffInvite.objects.select_related("claimed_by", "employee__user").get(id=invite_id)
    telegram_id = invite.claimed_by.telegram_id
    if invite.status == StaffInvite.Status.ACCEPTED:
        employee = EmployeeProfile.objects.select_related("user", "telegram_account").get(
            id=invite.employee_id
        )
        speaking = speaking_to_employee(employee)
    else:
        guest = GuestProfile.objects.filter(
            partner_id=invite.partner_id, telegram_account=invite.claimed_by
        ).select_related("telegram_account").first()
        speaking = speaking_to_guest(guest) if guest is not None else nullcontext()
    with speaking:
        # Button labels are read when the content is built: in their language.
        content = BotContent.for_partner(partner)
        keyboard = build_main_keyboard(
            content,
            navigation_state=resolve_guest_navigation_state(
                partner_id=partner.id, telegram_id=telegram_id, content=content
            ),
        )
        if invite.status == StaffInvite.Status.ACCEPTED:
            text = _(
                "Готово: вы в команде «{partner_name}» — {role}.\n"
                "Рабочий режим — кнопка «{button_staff_label}» внизу.",
                partner_name=escape_html(partner.name),
                role=escape_html(_role_label(invite.role)),
                button_staff_label=escape_html(content.button_staff_label),
            )
        else:
            text = _(
                "Заявку в команду «{partner_name}» отклонили.",
                partner_name=escape_html(partner.name),
            )
    return telegram_id, text, keyboard


@router.callback_query(
    lambda c: (c.data or "").startswith((f"{INVITE_CALLBACK}:y:", f"{INVITE_CALLBACK}:n:"))
)
async def invite_decision_handler(callback: CallbackQuery, bot: Bot) -> None:
    _prefix, verdict, invite_hex = callback.data.split(":", 2)
    try:
        invite_id = UUID(hex=invite_hex)
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        invite = await sync_to_async(decide_staff_invite)(
            partner_id=partner.id, invite_id=invite_id, actor=employee.user,
            accept=verdict == "y",
        )
    except ValueError:
        await callback.answer()
        return
    except (OrderFlowError, StaffInviteError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    person = await sync_to_async(lambda: _person_label(invite.claimed_by))()
    if invite.status == StaffInvite.Status.ACCEPTED:
        text = _(
            "✅ {person} теперь в команде: {role}.",
            person=escape_html(person),
            role=escape_html(_role_label(invite.role)),
        )
    else:
        text = _("✖ Заявка {person} отклонена.", person=escape_html(person))
    await _safe_edit_message_text(callback.message, text)
    await callback.answer()
    await _send(bot, *await sync_to_async(_answer_to_person)(invite.id, partner))
