from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from asgiref.sync import sync_to_async

from apps.partners.models import Partner
from bot.services.content import BotContent
from bot.services.context import (
    BotRuntimeDisabledError,
    PartnerSuspendedError,
    checked_partner,
    load_update_bot,
    partner_for_this_update,
)
from bot.services.language import language_context
from bot.services.network import (
    UpdateFacts,
    network_update,
    network_update_point,
    point_choice_markup,
    point_choice_text,
)
from core.i18n import N_, _, default_language, texts

SUSPENDED_PARTNER_MESSAGE = N_("Работа бота приостановлена, ожидайте обновлений.")


class PartnerRuntimeMiddleware(BaseMiddleware):
    """Enforce the current partner and bot runtime state before router dispatch.

    The handler then runs in the person's language with the venue's own texts
    (core/i18n.py, PQ-040).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot = data["bot"]
        try:
            network, partner, suspended, (language, overrides) = await sync_to_async(
                _start_of_update
            )(bot.token, _facts(event), data.get("event_from_user"))
        except BotRuntimeDisabledError:
            # Polling transport remains alive until the process is restarted,
            # but a live deactivation must not process or acknowledge updates.
            return None
        if network is not None:
            return await self._network_update(handler, event, data, *network)
        if suspended:
            with texts(language, overrides):
                await self._answer_suspended_update(event)
            return None
        data["language"] = language
        with texts(language, overrides), partner_for_this_update(bot.token, partner):
            return await handler(event, data)

    async def _network_update(self, handler, event, data, bot_instance, point):
        """A network's bot (PQ-047): run the update as the chosen venue's,
        or ask which venue."""

        bot = data["bot"]
        partner = point.partner or bot_instance.partner
        language, overrides = await self._language(partner, data)
        with network_update(bot_instance.id, len(point.points), partner.id), texts(
            language, overrides
        ):
            if point.partner is None or (
                point.partner.status != Partner.Status.ACTIVE and not point.points
            ):
                await self._ask_point(bot, event, point.points)
                return None
            if point.partner.status != Partner.Status.ACTIVE:
                await self._answer_suspended_update(event)
                await self._ask_point(bot, event, point.points)
                return None
            data["language"] = language
            # A /start link that waited for the pick (bot/handlers/points.py).
            data["pending_start"] = point.pending_start
            with partner_for_this_update(bot.token, point.partner):
                return await handler(event, data)

    @staticmethod
    async def _ask_point(bot, event, points) -> None:
        if not points:
            await PartnerRuntimeMiddleware._answer_suspended_update(event)
            return
        callback_query = getattr(event, "callback_query", None)
        if callback_query is not None:
            await bot.answer_callback_query(callback_query.id)
        chat_id = _chat_id(event)
        if chat_id is None:
            return
        content = BotContent.defaults()
        await bot.send_message(
            chat_id,
            point_choice_text(content.button_point_label),
            reply_markup=point_choice_markup(points),
        )

    @staticmethod
    async def _language(partner, data: dict[str, Any]):
        return await sync_to_async(_language_for)(partner, data.get("event_from_user"))

    @staticmethod
    async def _answer_suspended_update(event: TelegramObject) -> None:
        callback_query = getattr(event, "callback_query", None)
        if callback_query is not None:
            await callback_query.answer(_(SUSPENDED_PARTNER_MESSAGE), show_alert=True)
            return

        message = getattr(event, "message", None)
        if message is not None:
            await message.answer(_(SUSPENDED_PARTNER_MESSAGE))


def _language_for(partner, user):
    if partner is None:
        return default_language(), {}
    return language_context(
        partner, getattr(user, "id", None), getattr(user, "language_code", None)
    )


def _start_of_update(bot_token: str, facts: UpdateFacts, user):
    """One trip to the database thread at the start of an update: the bot
    (read once), a network's venue or the checked partner, and the language
    (load test 2026-11-05). Returns (network, partner, suspended, language)."""

    bot_instance = load_update_bot(bot_token)
    network = network_update_point(bot_token, facts, bot_instance=bot_instance)
    if network is not None:
        return network, None, False, (None, None)
    try:
        partner = checked_partner(bot_token, bot_instance)
    except PartnerSuspendedError as exc:
        return None, exc.partner, True, _language_for(exc.partner, user)
    return None, partner, False, _language_for(partner, user)


def _chat_id(event) -> int | None:
    callback_query = getattr(event, "callback_query", None)
    message = getattr(event, "message", None)
    if callback_query is not None and getattr(callback_query, "message", None) is not None:
        return callback_query.message.chat.id
    if message is not None:
        return message.chat.id
    return None


def _facts(event) -> UpdateFacts:
    """What decides the venue of an update of a network's bot."""

    callback_query = getattr(event, "callback_query", None)
    if callback_query is not None:
        pressed = getattr(callback_query, "message", None)
        return UpdateFacts(
            telegram_id=getattr(getattr(callback_query, "from_user", None), "id", None),
            chat_id=pressed.chat.id if pressed is not None else None,
            message_id=pressed.message_id if pressed is not None else None,
            callback_data=getattr(callback_query, "data", "") or "",
        )
    message = getattr(event, "message", None)
    if message is not None:
        replied = getattr(message, "reply_to_message", None)
        text = getattr(message, "text", "") or ""
        payload = ""
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            payload = parts[1].strip() if len(parts) == 2 else ""
        user = getattr(message, "from_user", None)
        return UpdateFacts(
            telegram_id=getattr(user, "id", None),
            chat_id=replied.chat.id if replied is not None else None,
            message_id=replied.message_id if replied is not None else None,
            start_payload=payload,
        )
    user = None
    for name in ("my_chat_member", "edited_message", "pre_checkout_query", "inline_query"):
        value = getattr(event, name, None)
        if value is not None:
            user = getattr(value, "from_user", None)
            break
    return UpdateFacts(telegram_id=getattr(user, "id", None))
