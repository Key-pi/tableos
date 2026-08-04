from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from asgiref.sync import sync_to_async

from bot.services.context import (
    BotRuntimeDisabledError,
    PartnerSuspendedError,
    resolve_partner_for_bot_token,
)

SUSPENDED_PARTNER_MESSAGE = "Работа бота приостановлена, ожидайте обновлений."


class PartnerRuntimeMiddleware(BaseMiddleware):
    """Enforce the current partner and bot runtime state before router dispatch."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot = data["bot"]
        try:
            await sync_to_async(resolve_partner_for_bot_token)(bot.token)
        except BotRuntimeDisabledError:
            # Polling transport remains alive until the process is restarted,
            # but a live deactivation must not process or acknowledge updates.
            return None
        except PartnerSuspendedError:
            await self._answer_suspended_update(event)
            return None
        return await handler(event, data)

    @staticmethod
    async def _answer_suspended_update(event: TelegramObject) -> None:
        callback_query = getattr(event, "callback_query", None)
        if callback_query is not None:
            await callback_query.answer(SUSPENDED_PARTNER_MESSAGE, show_alert=True)
            return

        message = getattr(event, "message", None)
        if message is not None:
            await message.answer(SUSPENDED_PARTNER_MESSAGE)
