import asyncio
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from bot.main import run_polling
from bot.services.runtime import BotRuntimeConfigError, load_active_bot_configs


class Command(BaseCommand):
    help = "Run all active Telegram bots via aiogram polling."

    def add_arguments(self, parser):
        parser.add_argument(
            "--wait",
            action="store_true",
            help=(
                "Не выходить, пока нет ни одного рабочего бота (или настройка ботов "
                "некорректна): перепроверять каждые --wait-interval секунд. Для Docker, "
                "где бота добавляют в Admin после запуска."
            ),
        )
        parser.add_argument("--wait-interval", type=int, default=30)

    def handle(self, *args, **options):
        wait = options["wait"]
        interval = max(1, options["wait_interval"])
        while True:
            problem = ""
            configs = []
            try:
                configs = load_active_bot_configs()
            except BotRuntimeConfigError as exc:
                if not wait:
                    raise CommandError(f"Bot runtime configuration is invalid:\n{exc}") from exc
                problem = f"Настройка ботов некорректна:\n{exc}"
            if configs:
                break
            if not wait:
                raise CommandError("No active bot instances configured.")
            if not problem:
                problem = (
                    "Нет активных ботов с настоящим токеном. Добавьте токен в .env "
                    "(DEMO_*_BOT_TOKEN) или в Admin → Partners → Bot instances."
                )
            self.stderr.write(f"{problem}\nПовторная проверка через {interval} с.")
            # The waiting process holds no transaction; drop the connection so a
            # long wait does not keep a stale one.
            close_old_connections()
            time.sleep(interval)

        names = ", ".join(f"@{config.username}" for config in configs)
        self.stdout.write(f"Запускаю polling: {names}")
        asyncio.run(run_polling(configs))
