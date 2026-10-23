"""`runbot --wait`: the bot container waits for a usable bot instead of crash-looping."""

from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.partners.models import BotInstance, Partner

VALID_TOKEN = "123456789:AAEexampleexampleexampleexample"


def _partner() -> Partner:
    return Partner.objects.create(
        name="Wait Venue", slug="wait-venue", status=Partner.Status.ACTIVE
    )


def _bot(partner: Partner, *, token: str, is_active: bool = True) -> BotInstance:
    bot = BotInstance(
        partner=partner,
        display_name="Wait Bot",
        username="wait_venue_bot",
        mode=BotInstance.Mode.POLLING,
        is_active=is_active,
    )
    bot.set_token(token)
    bot.save()
    return bot


class _StopWaiting(Exception):
    pass


# The waiting loop drops its DB connection between checks; inside a test
# transaction that would close the test connection, so it is stubbed here.
@patch("bot.management.commands.runbot.close_old_connections", lambda: None)
class RunbotWaitTests(TestCase):
    def test_without_wait_no_bots_is_an_error(self):
        with self.assertRaisesMessage(CommandError, "No active bot instances"):
            call_command("runbot", stdout=StringIO(), stderr=StringIO())

    def test_without_wait_invalid_config_is_an_error(self):
        _bot(_partner(), token="seed-placeholder-token-wait")
        with self.assertRaisesMessage(CommandError, "placeholder"):
            call_command("runbot", stdout=StringIO(), stderr=StringIO())

    def test_wait_polls_until_a_usable_bot_appears(self):
        partner = _partner()
        placeholder = _bot(partner, token="seed-placeholder-token-wait")
        sleeps: list[int] = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 1:
                # First check: a placeholder token is waited on, not fatal.
                placeholder.set_token(VALID_TOKEN)
                placeholder.save()
            else:  # pragma: no cover - the loop must have ended by now
                raise _StopWaiting

        stderr = StringIO()
        with (
            patch("bot.management.commands.runbot.time.sleep", side_effect=fake_sleep),
            patch("bot.management.commands.runbot.run_polling") as run_polling,
        ):
            run_polling.return_value = None
            call_command(
                "runbot", "--wait", "--wait-interval", "7", stdout=StringIO(), stderr=stderr
            )

        self.assertEqual(sleeps, [7])
        configs = run_polling.call_args.args[0]
        self.assertEqual([config.username for config in configs], ["wait_venue_bot"])
        self.assertIn("placeholder", stderr.getvalue())

    def test_wait_with_no_bots_keeps_waiting(self):
        sleeps: list[int] = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise _StopWaiting

        stderr = StringIO()
        with (
            patch("bot.management.commands.runbot.time.sleep", side_effect=fake_sleep),
            patch("bot.management.commands.runbot.run_polling") as run_polling,
            self.assertRaises(_StopWaiting),
        ):
            call_command("runbot", "--wait", stdout=StringIO(), stderr=stderr)

        run_polling.assert_not_called()
        self.assertEqual(sleeps, [30, 30])
        self.assertIn("Нет активных ботов", stderr.getvalue())
