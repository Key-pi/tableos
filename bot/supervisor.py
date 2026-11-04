"""The bot service: every bot polled on its own, the list re-read every minute.

Owner's decisions 2026-11-04:

- PQ-041: a broken bot (bad, duplicated or placeholder token) is skipped and
  its state shown in Admin (``BotInstance.runtime_state``); the others run.
- PQ-042: a bot added, switched off or given a new token in Admin is picked
  up at the next check (``interval``, a minute), without a restart.
- A token Telegram refuses while running ("Unauthorized") stops only that
  bot; it starts again once the token in Admin changes.

aiogram's ``start_polling`` cannot do this: one failing ``getMe`` stops all
bots and a refused token is retried forever. Each bot gets its own loop of
``getUpdates`` here, updates go to one shared dispatcher (``feed_update``).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import signal
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError, TelegramUnauthorizedError
from aiogram.methods import GetUpdates, TelegramMethod
from aiogram.utils.backoff import Backoff, BackoffConfig
from asgiref.sync import sync_to_async
from django.db import close_old_connections

from apps.partners.models import BotInstance
from apps.partners.services import record_bot_runtime_states
from bot.main import create_dispatcher
from bot.middlewares.message_points import MessagePointMiddleware
from bot.services.runtime import (
    BotRuntimeConfig,
    BotScan,
    configs_for_shard,
    register_runtime_configs,
    scan_bot_configs,
)

logger = logging.getLogger(__name__)
State = BotInstance.RuntimeState
BACKOFF = BackoffConfig(min_delay=1.0, max_delay=5.0, factor=1.3, jitter=0.1)


@functools.cache
def shared_dispatcher():
    """aiogram routers are module-level and attach to one dispatcher only,
    so a process has one dispatcher for all its bots."""

    return create_dispatcher()


def make_bot(token: str) -> Bot:
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    # A network's bot remembers the venue of each message it sends (PQ-047).
    bot.session.middleware(MessagePointMiddleware())
    return bot


@dataclass(slots=True)
class RunningBot:
    config: BotRuntimeConfig
    bot: Bot
    task: asyncio.Task | None = None
    updates: set[asyncio.Task] = field(default_factory=set)


class BotSupervisor:
    def __init__(
        self,
        *,
        shard_index: int = 0,
        shard_count: int = 1,
        interval: float = 60,
        polling_timeout: int = 30,
        scan: Callable[[], BotScan] = scan_bot_configs,
        bot_factory: Callable[[str], Bot] = make_bot,
    ) -> None:
        self.shard_index = shard_index
        self.shard_count = shard_count
        self.interval = interval
        self.polling_timeout = polling_timeout
        self.scan = scan
        self.bot_factory = bot_factory
        self.dispatcher = shared_dispatcher()
        self.allowed_updates = self.dispatcher.resolve_used_update_types()
        self.running: dict[str, RunningBot] = {}
        # bot instance id -> the token Telegram refused; not retried until it changes
        self.rejected: dict[str, str] = {}
        self.states: dict[str, str] = {}
        self._recorded: dict[str, str] = {}
        # Bots whose first poll Telegram answered.
        self._confirmed: set[str] = set()
        self._scan_states: dict[str, str] = {}
        self._told_idle = False
        self._stop = asyncio.Event()

    # --- the check every minute ---------------------------------------------

    def _mine(self, bot_instance_id: str) -> bool:
        return bool(
            configs_for_shard(
                [BotRuntimeConfig("", bot_instance_id, "", "")],
                index=self.shard_index,
                count=self.shard_count,
            )
        )

    async def reconcile(self) -> None:
        """Start new bots, stop removed ones, restart bots with a new token."""

        scan = await sync_to_async(self.scan)()
        wanted: dict[str, BotRuntimeConfig] = {}
        for config in configs_for_shard(
            scan.configs, index=self.shard_index, count=self.shard_count
        ):
            if self.rejected.get(config.bot_instance_id) == config.token:
                continue
            self.rejected.pop(config.bot_instance_id, None)
            wanted[config.bot_instance_id] = config
        for instance_id, reason in scan.problems.items():
            if self._mine(instance_id) and self._recorded.get(instance_id) != scan.states.get(
                instance_id
            ):
                logger.warning("Bot not started: %s", reason)
        if not wanted and not self.running and not self._told_idle:
            logger.warning(
                "No active bots with a real token yet; add one in Admin → Боты. "
                "Checking again every %s s.",
                self.interval,
            )
        self._told_idle = not wanted

        for instance_id, running in list(self.running.items()):
            config = wanted.get(instance_id)
            if config is None or config.token != running.config.token:
                await self._stop_bot(instance_id)
        # The middleware finds a bot's partner by its token in this registry.
        register_runtime_configs(list(wanted.values()))
        for instance_id, config in wanted.items():
            if instance_id not in self.running:
                self._start_bot(config)

        self._scan_states = {
            instance_id: state
            for instance_id, state in scan.states.items()
            if self._mine(instance_id)
        }
        for instance_id, state in self._scan_states.items():
            self.states[instance_id] = self._state_of(instance_id, state)
        await self._record()

    def _state_of(self, instance_id: str, scanned: str) -> str:
        """What Admin shows: a refused token, a conflict, running (once
        Telegram answered the first poll) or why the scan skipped the bot."""

        if instance_id in self.rejected:
            return State.TOKEN_REJECTED
        if instance_id not in self.running:
            return scanned
        if self.states.get(instance_id) == State.CONFLICT:
            return State.CONFLICT
        if instance_id in self._confirmed:
            return scanned
        return ""  # the first poll is still on its way: nothing to record yet

    async def _record(self) -> None:
        changed = {
            instance_id: state
            for instance_id, state in self.states.items()
            if state and self._recorded.get(instance_id) != state
        }
        if not changed:
            return
        await sync_to_async(record_bot_runtime_states)(changed)
        self._recorded.update(changed)

    # --- one bot ---------------------------------------------------------------

    def _start_bot(self, config: BotRuntimeConfig) -> None:
        running = RunningBot(config=config, bot=self.bot_factory(config.token))
        running.task = asyncio.create_task(self._poll(running), name=f"poll @{config.username}")
        self.running[config.bot_instance_id] = running
        logger.info("Polling @%s", config.username)

    async def _stop_bot(self, instance_id: str) -> None:
        running = self.running.pop(instance_id, None)
        if running is None:
            return
        if running.task is not None and running.task is not asyncio.current_task():
            running.task.cancel()
            with suppress(asyncio.CancelledError):
                await running.task
        # Let updates in work finish before the HTTP session goes.
        if running.updates:
            await asyncio.wait(running.updates, timeout=10)
        await running.bot.session.close()
        self._confirmed.discard(instance_id)
        logger.info("Stopped @%s", running.config.username)

    async def _poll(self, running: RunningBot) -> None:
        config = running.config
        instance_id = config.bot_instance_id
        # The first request answers at once: it confirms the token for Admin
        # without waiting a long poll for messages.
        get_updates = GetUpdates(timeout=0, allowed_updates=self.allowed_updates)
        backoff = Backoff(config=BACKOFF)
        while True:
            try:
                updates = await running.bot(
                    get_updates, request_timeout=self.polling_timeout + 30
                )
            except asyncio.CancelledError:
                raise
            except TelegramUnauthorizedError:
                # A token revoked in @BotFather: stop only this bot until a new
                # token is saved in Admin.
                logger.error("Telegram refused the token of @%s; bot stopped.", config.username)
                self.rejected[instance_id] = config.token
                self.states[instance_id] = State.TOKEN_REJECTED
                await self._record()
                # From inside its own task: _stop_bot does not cancel itself.
                await self._stop_bot(instance_id)
                return
            except TelegramConflictError:
                if self.states.get(instance_id) != State.CONFLICT:
                    logger.error("Another program polls the token of @%s.", config.username)
                    self.states[instance_id] = State.CONFLICT
                    await self._record()
                await backoff.asleep()
                continue
            except Exception as exc:  # noqa: BLE001 - network or Telegram outage: keep trying
                logger.warning("Polling @%s failed: %s: %s", config.username,
                               type(exc).__name__, exc)
                await backoff.asleep()
                continue
            backoff.reset()
            get_updates.timeout = self.polling_timeout
            if instance_id not in self._confirmed or self.states.get(instance_id) == State.CONFLICT:
                self._confirmed.add(instance_id)
                self.states[instance_id] = self._running_state(instance_id)
                await self._record()
            for update in updates:
                task = asyncio.create_task(self._handle(running.bot, update))
                running.updates.add(task)
                task.add_done_callback(running.updates.discard)
                get_updates.offset = update.update_id + 1

    def _running_state(self, instance_id: str) -> str:
        return self._scan_states.get(instance_id, State.RUNNING)

    async def _handle(self, bot: Bot, update) -> None:
        try:
            response = await self.dispatcher.feed_update(bot, update)
            if isinstance(response, TelegramMethod):
                await self.dispatcher.silent_call_request(bot=bot, result=response)
        except Exception:  # noqa: BLE001 - one bad update must not stop the bot
            logger.exception("Update %s of bot id=%s failed", update.update_id, bot.id)

    # --- the service -----------------------------------------------------------

    async def run(self, *, handle_signals: bool = True) -> None:
        if handle_signals:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                with suppress(NotImplementedError):
                    loop.add_signal_handler(sig, self._stop.set)
        try:
            while not self._stop.is_set():
                try:
                    await self.reconcile()
                except Exception:  # noqa: BLE001 - e.g. the database is restarting
                    logger.exception("Checking the bots failed; retrying at the next check.")
                # The loop waits without a transaction; drop a stale connection.
                await sync_to_async(close_old_connections)()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
        finally:
            for instance_id in list(self.running):
                await self._stop_bot(instance_id)

    def stop(self) -> None:
        self._stop.set()
