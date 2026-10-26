"""`runbot` in several processes: each partner's bot is polled by exactly one.

One process handles every update of every bot in one database thread, so it
uses one CPU core; on 2 cores 20 partners at peak already waited 1.3 s, with
the bots split over 3 processes 30 partners got 0.8 s (load test 2026-10-25).
Telegram allows one poller per token, so the split must never give a bot to
two processes.
"""

from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from apps.partners.models import BotInstance, Partner
from bot.services.runtime import BotRuntimeConfig, configs_for_shard


def _config(n: int) -> BotRuntimeConfig:
    return BotRuntimeConfig(
        partner_id=f"00000000-0000-0000-0000-{n:012d}",
        bot_instance_id=f"10000000-0000-0000-0000-{n:012d}",
        username=f"bot_{n}",
        token=f"{100000000 + n}:AAEexampleexampleexampleexample",
    )


class ConfigsForShardTests(SimpleTestCase):
    def test_every_bot_goes_to_exactly_one_shard(self):
        configs = [_config(n) for n in range(40)]
        shards = [configs_for_shard(configs, index=k, count=3) for k in range(3)]

        tokens = [config.token for shard in shards for config in shard]
        self.assertCountEqual(tokens, [config.token for config in configs])
        self.assertEqual(len(tokens), len(set(tokens)))
        self.assertTrue(all(shards), "40 bots over 3 shards leave no shard empty")

    def test_split_does_not_depend_on_order_or_on_other_bots(self):
        configs = [_config(n) for n in range(12)]
        first = {c.token for c in configs_for_shard(configs, index=1, count=3)}
        reordered = {c.token for c in configs_for_shard(configs[::-1], index=1, count=3)}
        with_new_bot = {
            c.token for c in configs_for_shard(configs + [_config(99)], index=1, count=3)
        }
        self.assertEqual(first, reordered)
        self.assertEqual(first, with_new_bot - {_config(99).token})

    def test_single_shard_keeps_every_bot(self):
        configs = [_config(n) for n in range(5)]
        self.assertEqual(configs_for_shard(configs, index=0, count=1), configs)


def _active_bot(n: int) -> BotInstance:
    partner = Partner.objects.create(
        name=f"Shard Venue {n}", slug=f"shard-venue-{n}", status=Partner.Status.ACTIVE
    )
    bot = BotInstance(
        partner=partner,
        display_name=f"Shard Bot {n}",
        username=f"shard_bot_{n}",
        mode=BotInstance.Mode.POLLING,
        is_active=True,
    )
    bot.set_token(f"{200000000 + n}:AAEexampleexampleexampleexample")
    bot.save()
    return bot


@patch("bot.management.commands.runbot.close_old_connections", lambda: None)
class RunbotShardTests(TestCase):
    def test_shard_polls_only_its_own_bots(self):
        for n in range(6):
            _active_bot(n)
        polled: list[set[str]] = []
        with patch("bot.management.commands.runbot.run_polling") as run_polling:
            run_polling.return_value = None
            for k in range(2):
                call_command(
                    "runbot", "--shard-index", str(k), "--shard-count", "2",
                    stdout=StringIO(), stderr=StringIO(),
                )
                polled.append({c.username for c in run_polling.call_args.args[0]})

        self.assertEqual(polled[0] | polled[1], {f"shard_bot_{n}" for n in range(6)})
        self.assertEqual(polled[0] & polled[1], set())

    def test_without_shard_options_every_bot_is_polled(self):
        for n in range(3):
            _active_bot(n)
        with patch("bot.management.commands.runbot.run_polling") as run_polling:
            run_polling.return_value = None
            call_command("runbot", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(len(run_polling.call_args.args[0]), 3)

    def test_shard_index_must_be_below_count(self):
        with self.assertRaisesMessage(CommandError, "--shard-index"):
            call_command(
                "runbot", "--shard-index", "2", "--shard-count", "2",
                stdout=StringIO(), stderr=StringIO(),
            )

    def test_processes_starts_one_shard_per_child(self):
        with (
            patch("bot.management.commands.runbot.subprocess.Popen") as popen,
            patch("bot.management.commands.runbot.signal.signal"),
            # A child that stops by itself fails the whole command (Docker
            # then restarts every bot), instead of leaving its bots silent.
            self.assertRaisesMessage(CommandError, "завершился сам"),
        ):
            popen.return_value.poll.return_value = 1
            call_command(
                "runbot", "--processes", "3", "--wait",
                stdout=StringIO(), stderr=StringIO(),
            )

        commands = [call.args[0] for call in popen.call_args_list]
        self.assertEqual(len(commands), 3)
        for k, command in enumerate(commands):
            self.assertIn("runbot", command)
            self.assertEqual(
                command[command.index("--shard-index") + 1], str(k)
            )
            self.assertEqual(command[command.index("--shard-count") + 1], "3")
            self.assertIn("--wait", command)
