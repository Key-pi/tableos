"""`runbot --wait`: the bot container waits for a usable bot instead of crash-looping."""

from io import StringIO
from unittest.mock import AsyncMock, patch

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


class RunbotStartTests(TestCase):
    """The service starts through bot/supervisor.py, which re-reads the bots
    every minute (PQ-042); a broken bot does not stop the others (PQ-041)."""

    def test_without_wait_no_bots_is_an_error(self):
        with self.assertRaisesMessage(CommandError, "No active bot instances"):
            call_command("runbot", stdout=StringIO(), stderr=StringIO())

    def test_without_wait_only_broken_bots_is_an_error(self):
        _bot(_partner(), token="seed-placeholder-token-wait")
        with self.assertRaisesMessage(CommandError, "placeholder"):
            call_command("runbot", stdout=StringIO(), stderr=StringIO())

    def test_a_broken_bot_next_to_a_working_one_does_not_stop_the_start(self):
        partner = _partner()
        _bot(partner, token="seed-placeholder-token-wait")
        good = BotInstance(
            partner=partner, display_name="Good", username="good_venue_bot", is_active=True
        )
        good.set_token(VALID_TOKEN)
        good.save()
        with patch("bot.management.commands.runbot.BotSupervisor") as supervisor:
            supervisor.return_value.run = AsyncMock()
            call_command("runbot", "--reload-interval", "15", stdout=StringIO())
        supervisor.assert_called_once_with(shard_index=0, shard_count=1, interval=15)
        supervisor.return_value.run.assert_awaited_once()

    def test_wait_starts_the_service_even_with_no_bots(self):
        # The service itself keeps checking for bots added in Admin.
        with patch("bot.management.commands.runbot.BotSupervisor") as supervisor:
            supervisor.return_value.run = AsyncMock()
            call_command("runbot", "--wait", stdout=StringIO())
        supervisor.assert_called_once_with(shard_index=0, shard_count=1, interval=60)
