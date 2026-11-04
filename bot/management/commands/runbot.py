import asyncio
import signal
import subprocess
import sys
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bot.services.runtime import configs_for_shard, scan_bot_configs
from bot.supervisor import BotSupervisor


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
        parser.add_argument(
            "--wait-interval",
            type=int,
            default=30,
            help="Оставлен для совместимости; ботов перечитывает --reload-interval.",
        )
        parser.add_argument(
            "--reload-interval",
            type=int,
            default=60,
            help=(
                "Раз в столько секунд перечитывать список ботов: новые запускаются, "
                "выключенные останавливаются, со сменённым токеном — перезапускаются (PQ-042)."
            ),
        )
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

        if not options["wait"]:
            # Without --wait the command still refuses to start with nothing to
            # run; a broken bot next to working ones no longer stops them (PQ-041).
            scan = scan_bot_configs()
            mine = configs_for_shard(scan.configs, index=shard_index, count=shard_count)
            if not mine:
                problems = "\n".join(scan.problems.values())
                raise CommandError(
                    f"No active bot instances configured{shard}."
                    + (f"\nBot runtime configuration is invalid:\n{problems}" if problems else "")
                )
        interval = max(1, options["reload_interval"])
        self.stdout.write(
            f"Запускаю ботов{shard}; список перечитывается каждые {interval} с."
        )
        asyncio.run(
            BotSupervisor(
                shard_index=shard_index, shard_count=shard_count, interval=interval
            ).run()
        )

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
            base += ["--wait"]
        base += ["--reload-interval", str(options["reload_interval"])]
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
