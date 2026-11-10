"""Counts bot updates for the platform's load page (PQ-051).

The time of an update — from the first middleware to the handler's end. The
counts go to Redis off the event loop, at most every 10 seconds
(``core/load_counter.py``)."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from core import load_counter


class LoadCounterMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        started = time.perf_counter()
        try:
            return await handler(event, data)
        finally:
            if load_counter.record("bot", time.perf_counter() - started):
                await asyncio.to_thread(load_counter.counter.flush)
