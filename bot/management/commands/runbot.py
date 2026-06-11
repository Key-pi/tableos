import asyncio

from django.core.management.base import BaseCommand, CommandError

from bot.main import run_polling
from bot.services.runtime import BotRuntimeConfigError, load_active_bot_configs


class Command(BaseCommand):
    help = "Run all active Telegram bots via aiogram polling."

    def handle(self, *args, **options):
        try:
            configs = load_active_bot_configs()
        except BotRuntimeConfigError as exc:
            raise CommandError(f"Bot runtime configuration is invalid:\n{exc}") from exc
        if not configs:
            raise CommandError("No active bot instances configured.")
        asyncio.run(run_polling(configs))
