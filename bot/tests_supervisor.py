"""The bot service (bot/supervisor.py): a broken bot never stops the others
(PQ-041), the list is re-read without a restart (PQ-042), a token Telegram
refuses stops only that bot until the token changes (owner, 2026-11-04)."""

import asyncio
import uuid

from aiogram.exceptions import TelegramConflictError, TelegramUnauthorizedError
from asgiref.sync import async_to_sync, sync_to_async
from django.test import TestCase

from apps.partners.models import BotInstance, Partner
from bot.supervisor import BotSupervisor

State = BotInstance.RuntimeState


class FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeBot:
    """Answers getUpdates the way ``behaviour[token]`` says: ``"ok"`` (no
    updates), ``"unauthorized"`` or ``"conflict"``."""

    behaviour: dict[str, str] = {}

    def __init__(self, token: str):
        self.token = token
        self.id = int(token.split(":")[0])
        self.session = FakeSession()
        self.polls = 0
        self.timeouts: list[int] = []

    async def __call__(self, method, request_timeout=None):
        self.polls += 1
        self.timeouts.append(method.timeout)
        await asyncio.sleep(0.01)
        mode = self.behaviour.get(self.token, "ok")
        if mode == "unauthorized":
            raise TelegramUnauthorizedError(method=method, message="Unauthorized")
        if mode == "conflict":
            raise TelegramConflictError(method=method, message="Conflict")
        return []


def make_partner(n: int, status=Partner.Status.ACTIVE) -> Partner:
    return Partner.objects.create(name=f"Sup {n}", slug=f"sup-{n}", status=status)


def make_bot(n: int, *, token=None, partner=None, bot_id=None) -> BotInstance:
    bot = BotInstance(
        id=bot_id or uuid.uuid4(),
        partner=partner or make_partner(n),
        display_name=f"Sup bot {n}",
        username=f"sup_bot_{n}",
        mode=BotInstance.Mode.POLLING,
        is_active=True,
    )
    bot.set_token(token or f"{300000000 + n}:AAEexampleexampleexampleexample")
    bot.save()
    return bot


class SupervisorTests(TestCase):
    def setUp(self):
        FakeBot.behaviour = {}
        self.made: list[FakeBot] = []

    def factory(self, token):
        bot = FakeBot(token)
        self.made.append(bot)
        return bot

    def run_checks(self, *steps, shard=(0, 1)):
        """Run ``steps`` (sync callables between checks) with a reconcile and
        a short pause after each; returns the supervisor (bots stopped)."""

        async def scenario():
            supervisor = BotSupervisor(
                shard_index=shard[0], shard_count=shard[1], bot_factory=self.factory
            )
            for step in (None, *steps):
                if step is not None:
                    await sync_to_async(step)()
                await supervisor.reconcile()
                await asyncio.sleep(0.08)
            self.running_after = set(supervisor.running)
            for instance_id in list(supervisor.running):
                await supervisor._stop_bot(instance_id)
            return supervisor

        return async_to_sync(scenario)()

    def state(self, bot):
        bot.refresh_from_db()
        return bot.runtime_state

    def test_broken_bots_are_skipped_and_shown_and_the_good_one_runs(self):
        good = make_bot(1)
        placeholder = make_bot(2, token="seed-placeholder-token-x")
        invalid = make_bot(3, token="not-a-token")
        duplicate = make_bot(4, token=f"{300000001}:AAEexampleexampleexampleexample")
        suspended = make_bot(5, partner=make_partner(5, status=Partner.Status.SUSPENDED))

        self.run_checks()

        self.assertEqual(self.running_after, {str(good.id), str(suspended.id)})
        self.assertEqual(self.state(good), State.RUNNING)
        self.assertEqual(self.state(placeholder), State.PLACEHOLDER_TOKEN)
        self.assertEqual(self.state(invalid), State.INVALID_TOKEN)
        self.assertEqual(self.state(duplicate), State.DUPLICATE_TOKEN)
        # A suspended venue's bot answers that its work is suspended (PQ-013).
        self.assertEqual(self.state(suspended), State.RUNNING_SUSPENDED)
        self.assertIsNotNone(good.runtime_checked_at)

    def test_the_first_request_answers_at_once_so_admin_shows_running_soon(self):
        # A long poll waits up to 30 s for messages; the first one must not,
        # or Admin keeps an old "token refused" for that long.
        make_bot(1)
        self.run_checks()
        timeouts = self.made[0].timeouts
        self.assertEqual(timeouts[0], 0)
        self.assertEqual(set(timeouts[1:]), {30})

    def test_bots_added_switched_off_and_given_a_new_token_are_picked_up(self):
        first = make_bot(1)
        later = {}

        def add_one():
            later["bot"] = make_bot(2)

        def switch_off_first_and_change_token_of_second():
            BotInstance.objects.filter(id=first.id).update(is_active=False)
            second = later["bot"]
            second.set_token(f"{399999999}:AAEexampleexampleexampleexample")
            second.save()

        self.run_checks(add_one, switch_off_first_and_change_token_of_second)

        self.assertEqual(self.running_after, {str(later["bot"].id)})
        tokens = [bot.token for bot in self.made]
        self.assertEqual(len(tokens), 3, "first, second, second with its new token")
        old_second = next(bot for bot in self.made if bot.token.startswith("300000002"))
        self.assertTrue(old_second.session.closed)
        self.assertTrue(self.made[0].session.closed)

    def test_a_refused_token_stops_only_that_bot_until_it_changes(self):
        good = make_bot(1)
        revoked = make_bot(2)
        FakeBot.behaviour[revoked.token] = "unauthorized"

        def new_token():
            revoked.set_token(f"{388888888}:AAEexampleexampleexampleexample")
            revoked.save()

        supervisor = self.run_checks(lambda: None)
        self.assertEqual(self.running_after, {str(good.id)})
        self.assertEqual(self.state(revoked), State.TOKEN_REJECTED)
        # Not retried with the same token at the second check.
        revoked_token = f"{300000002}:AAEexampleexampleexampleexample"
        self.assertEqual(sum(1 for bot in self.made if bot.token == revoked_token), 1)
        self.assertIn(str(revoked.id), supervisor.rejected)

        self.made.clear()
        self.run_checks(new_token)
        self.assertIn(str(revoked.id), self.running_after)
        self.assertEqual(self.state(revoked), State.RUNNING)

    def test_a_conflict_is_shown_and_clears_when_polling_works_again(self):
        bot = make_bot(1)
        FakeBot.behaviour[bot.token] = "conflict"

        def resolved():
            FakeBot.behaviour[bot.token] = "ok"

        async def scenario():
            supervisor = BotSupervisor(bot_factory=self.factory)
            await supervisor.reconcile()
            await asyncio.sleep(0.05)
            state_during = await sync_to_async(self.state)(bot)
            resolved()
            await asyncio.sleep(1.5)  # backoff of the failed poll
            state_after = await sync_to_async(self.state)(bot)
            for instance_id in list(supervisor.running):
                await supervisor._stop_bot(instance_id)
            return state_during, state_after

        during, after = async_to_sync(scenario)()
        self.assertEqual(during, State.CONFLICT)
        self.assertEqual(after, State.RUNNING)

    def test_each_process_runs_only_its_own_bots(self):
        bots = [make_bot(n, bot_id=uuid.UUID(int=1000 + n)) for n in range(6)]
        running = []
        for index in range(2):
            self.run_checks(shard=(index, 2))
            running.append(self.running_after)
        self.assertEqual(running[0] | running[1], {str(bot.id) for bot in bots})
        self.assertEqual(running[0] & running[1], set())
