"""Picking the venue in a network's bot (PQ-047, stage 2).

The middleware already saved the pick of ``point:<hex>`` and runs this
update as that venue's; here the guest only gets the venue's greeting.
"""

from __future__ import annotations

from types import SimpleNamespace

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.partners.networks import network_points
from bot.filters.content import PartnerButtonFilter
from bot.handlers import start
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.network import (
    POINT_CALLBACK_PREFIX,
    point_choice_markup,
    point_choice_text,
    update_points_count,
)
from bot.utils.html import escape_html
from core.i18n import _

router = Router(name="points")


@router.callback_query(lambda c: (c.data or "").startswith(POINT_CALLBACK_PREFIX))
async def point_picked_handler(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    await callback.answer()
    await callback.message.edit_text(
        _("📍 {partner_name}", partner_name=escape_html(partner.name)), reply_markup=None
    )
    # The same greeting as /start, now for this venue.
    greeting = SimpleNamespace(
        text="/start", from_user=callback.from_user, answer=callback.message.answer
    )
    await start.start_handler(greeting, SimpleNamespace(args=""), bot)


@router.message(Command("point"))
@router.message(PartnerButtonFilter("button_point_label"))
async def point_choice_handler(message: Message, bot: Bot) -> None:
    if update_points_count() < 2:
        return  # not a network's bot with several venues
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    points = await sync_to_async(network_points)(partner.network_id)
    await message.answer(
        point_choice_text(content.button_point_label), reply_markup=point_choice_markup(points)
    )
