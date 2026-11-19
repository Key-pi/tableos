"""«🔐 Войти в Admin» in work mode (PQ-045, step 2; owner 2026-11-19).

The owner and staff the owner gave Admin get a one-time link for 15 minutes;
rules live in ``apps/users/admin_login.py``. The message has no link preview:
nothing opens the address before the person does (the page asks «Войти»
anyway). Telegram takes only public HTTPS addresses in buttons, so without
one (locally) the link is in the text.

Callback: ``staffadmin``.
"""

from __future__ import annotations

from aiogram import Bot, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
)
from asgiref.sync import sync_to_async

from apps.orders.services import OrderFlowError
from apps.users.admin_login import AdminLoginError, admin_login_url, issue_admin_login_link
from bot.handlers.staff import _resolve_staff_employee_from_callback
from bot.utils.html import escape_html
from core.i18n import _

router = Router(name="staff_admin_login")

ADMIN_LOGIN_CALLBACK = "staffadmin"


@router.callback_query(lambda c: c.data == ADMIN_LOGIN_CALLBACK)
async def admin_login_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        raw = await sync_to_async(issue_admin_login_link)(partner_id=partner.id, employee=employee)
    except (OrderFlowError, AdminLoginError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    url = await sync_to_async(admin_login_url)(raw)
    note = _("Ссылка одноразовая и действует 15 минут. Не пересылайте её: "
             "кто откроет — войдёт под вашим именем.")
    if url.startswith("https://"):
        text = _("<b>Вход в Admin</b>\n{note}", note=note)
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=_("🔐 Открыть Admin"), url=url)],
        ])
    else:
        text = _("<b>Вход в Admin</b>\n{link}\n\n{note}", link=escape_html(url), note=note)
        markup = None
    await callback.message.answer(
        text, reply_markup=markup, link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    await callback.answer()
