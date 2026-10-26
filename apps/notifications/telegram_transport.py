"""One connection to Telegram per worker process, kept between deliveries.

``async_to_sync`` runs every delivery on a fresh event loop, and an HTTP
session cannot outlive its loop: each notification opened a new TLS
connection to Telegram and waited 0.25 s while aiogram closed it. That capped
a two-process worker at about five notifications a second (load test
2026-10-25, AA-044).

Bots and their shared HTTP session live on one long-lived event loop in a
daemon thread of the process. A delivery still runs on its own loop (so
``sync_to_async`` callbacks keep their thread and database connection) and
only hands each Telegram request to that loop. Telegram exceptions travel back
unchanged. A forked worker child starts its own loop on first use.
"""

from __future__ import annotations

import asyncio
import os
import threading

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

_lock = threading.Lock()
_state: dict = {"pid": None, "loop": None, "session": None, "bots": {}}


def _sender_loop() -> asyncio.AbstractEventLoop:
    with _lock:
        if _state["pid"] != os.getpid() or _state["loop"] is None:
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever, name="telegram-sender", daemon=True
            ).start()
            _state.update(pid=os.getpid(), loop=loop, session=None, bots={})
        return _state["loop"]


def _bot(token: str) -> Bot:
    # Called on the sender loop only, so the session is created there.
    bots = _state["bots"]
    bot = bots.get(token)
    if bot is None:
        if _state["session"] is None:
            _state["session"] = AiohttpSession()
        bot = Bot(
            token=token,
            session=_state["session"],
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        bots[token] = bot
    return bot


async def send_message(token: str, *, chat_id: int, text: str, reply_markup=None):
    """``Bot.send_message`` over the process-wide connection."""

    async def on_sender_loop():
        return await _bot(token).send_message(
            chat_id=chat_id, text=text, reply_markup=reply_markup
        )

    future = asyncio.run_coroutine_threadsafe(on_sender_loop(), _sender_loop())
    return await asyncio.wrap_future(future)
