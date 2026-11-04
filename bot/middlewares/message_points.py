"""Remember which venue a network bot's message was sent for (PQ-047).

A request middleware of every bot's HTTP session: after a send or an edit
inside an update of a network's bot, the message's (chat, id) is stored with
the update's venue, so a button pressed on it later acts for that venue
(``apps/partners/networks.py:resolve_update_point``). Messages of a venue's
own bot are not stored.
"""

from __future__ import annotations

import logging

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.types import Message
from asgiref.sync import sync_to_async

from apps.partners.networks import remember_message_point
from bot.services.network import update_message_point

logger = logging.getLogger(__name__)


class MessagePointMiddleware(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        result = await make_request(bot, method)
        point = update_message_point()
        message = getattr(result, "result", result)
        if point is not None and isinstance(message, Message):
            bot_instance_id, partner_id = point
            try:
                await sync_to_async(remember_message_point)(
                    bot_instance_id, message.chat.id, message.message_id, partner_id
                )
            except Exception:  # noqa: BLE001 - the message is sent; a lost mark is not fatal
                logger.exception("Could not remember the venue of a network bot message")
        return result
