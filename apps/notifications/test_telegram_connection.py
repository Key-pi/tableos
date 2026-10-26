"""Notifications reuse one connection to Telegram per worker process.

Each delivery used to open a new HTTP session (a new TLS connection to
Telegram) and wait 0.25 s while closing it. With two worker processes that
capped the whole server at about five notifications a second, and at 20
partners in peak staff saw new orders 7 s late (load test 2026-10-25).
"""

from unittest.mock import patch

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from apps.notifications import services

TOKEN = "123456789:AAEexampleexampleexampleexample"


class TelegramConnectionReuseTests(SimpleTestCase):
    def setUp(self):
        self.sessions: list[int] = []
        self.closed: list[int] = []

        async def fake_request(session, bot, method, timeout=None):
            self.sessions.append(id(session))
            if method.chat_id == 13:
                raise TelegramForbiddenError(method=SendMessage(chat_id=13, text="x"),
                                             message="Forbidden: bot was blocked by the user")
            return None

        async def fake_close(session):
            self.closed.append(id(session))

        for target, fake in (("make_request", fake_request), ("close", fake_close)):
            patcher = patch.object(AiohttpSession, target, new=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_consecutive_deliveries_share_one_session_and_never_close_it(self):
        async_to_sync(services._send_telegram_messages)(TOKEN, [(11, "first")])
        async_to_sync(services._send_telegram_messages)(TOKEN, [(12, "second")])

        self.assertEqual(len(self.sessions), 2)
        self.assertEqual(len(set(self.sessions)), 1)
        self.assertEqual(self.closed, [])

    def test_telegram_errors_still_reach_the_caller(self):
        results = async_to_sync(services._send_telegram_messages)(
            TOKEN, [(11, "ok"), (13, "blocked")]
        )

        self.assertEqual(results[0][:2], (11, True))
        self.assertEqual(results[1][:4], (13, False, results[1][2], True))
        self.assertIn("blocked", results[1][2])
