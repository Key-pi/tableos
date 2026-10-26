import asyncio
import signal
import subprocess
import sys
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from bot.main import run_polling
from bot.services.runtime import (
    BotRuntimeConfigError,
    configs_for_shard,
    load_active_bot_configs,
)


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
        parser.add_argument(
            "--processes",
            type=int,
            default=1,
            help=(
                "Запустить ботов в N процессах (каждый бот — ровно в одном). Один процесс "
                "использует одно ядро процессора; на 2 ядрах — 2–3 процесса."
            ),
        )
        parser.add_argument(
            "--shard-index",
            type=int,
            default=0,
            help="Номер этого процесса среди --shard-count (с нуля). Обычно задаёт --processes.",
        )
        parser.add_argument("--shard-count", type=int, default=1)

    def handle(self, *args, **options):
        if options["processes"] > 1:
            return self._run_processes(options)
        shard_count = max(1, options["shard_count"])
        shard_index = options["shard_index"]
        if not 0 <= shard_index < shard_count:
            raise CommandError("--shard-index должен быть от 0 до --shard-count минус 1.")
        shard = f" (процесс {shard_index + 1} из {shard_count})" if shard_count > 1 else ""

        wait = options["wait"]
        interval = max(1, options["wait_interval"])
        while True:
            problem = ""
            configs = []
            try:
                configs = configs_for_shard(
                    load_active_bot_configs(), index=shard_index, count=shard_count
                )
            except BotRuntimeConfigError as exc:
                if not wait:
                    raise CommandError(f"Bot runtime configuration is invalid:\n{exc}") from exc
                problem = f"Настройка ботов некорректна:\n{exc}"
            if configs:
                break
            if not wait:
                raise CommandError(f"No active bot instances configured{shard}.")
            if not problem:
                problem = (
                    f"Нет активных ботов с настоящим токеном{shard}. Добавьте токен в .env "
                    "(DEMO_*_BOT_TOKEN) или в Admin → Partners → Bot instances."
                )
            self.stderr.write(f"{problem}\nПовторная проверка через {interval} с.")
            # The waiting process holds no transaction; drop the connection so a
            # long wait does not keep a stale one.
            close_old_connections()
            time.sleep(interval)

        names = ", ".join(f"@{config.username}" for config in configs)
        self.stdout.write(f"Запускаю polling{shard}: {names}")
        asyncio.run(run_polling(configs))

    def _run_processes(self, options) -> None:
        """Start one ``runbot`` per shard and stop them all when one stops.

        Docker restarts the container then, so a crashed shard does not leave
        its bots silent while the others keep running.
        """

        count = options["processes"]
        base = [
            sys.executable, str(settings.BASE_DIR / "manage.py"), "runbot",
            "--shard-count", str(count),
        ]
        if options["wait"]:
            base += ["--wait", "--wait-interval", str(options["wait_interval"])]
        children = [
            subprocess.Popen(base + ["--shard-index", str(index)]) for index in range(count)
        ]

        signalled = False

        def stop_children():
            for child in children:
                if child.poll() is None:
                    child.terminate()

        def on_sigterm(*_unused):
            nonlocal signalled
            signalled = True
            stop_children()

        previous = signal.signal(signal.SIGTERM, on_sigterm)
        try:
            while not signalled and all(child.poll() is None for child in children):
                time.sleep(1)
            codes = [child.poll() for child in children]
        finally:
            stop_children()
            for child in children:
                child.wait()
            signal.signal(signal.SIGTERM, previous)
        if not signalled:
            # A shard stopped by itself: fail, so Docker restarts all the bots.
            code = next(code for code in codes if code is not None)
            raise CommandError(f"Процесс бота завершился сам (код {code}).")
