"""Requests and bot updates counted per minute for the load page (PQ-051).

A process keeps its counts in memory and adds them to Redis at most every
10 seconds — one short write, never in the way of a request: with Redis
down the counts are dropped and the request goes on."""

import asyncio
from unittest import mock

from django.test import RequestFactory, SimpleTestCase, override_settings

from core import load_counter
from core.middleware import LoadCounterMiddleware


class FakeRedis:
    def __init__(self, broken=False):
        self.hashes: dict[str, dict[str, int]] = {}
        self.expiring: dict[str, int] = {}
        self.broken = broken

    def pipeline(self):
        return FakePipeline(self)

    def hgetall(self, key):
        if self.broken:
            raise ConnectionError("down")
        return {name.encode(): str(value).encode()
                for name, value in self.hashes.get(key, {}).items()}


class FakePipeline:
    def __init__(self, redis):
        self.redis, self.calls = redis, []

    def hincrby(self, key, field, amount):
        self.calls.append(("hincrby", key, field, amount))

    def expire(self, key, seconds):
        self.calls.append(("expire", key, seconds))

    def execute(self):
        if self.redis.broken:
            raise ConnectionError("down")
        for call in self.calls:
            if call[0] == "hincrby":
                bucket = self.redis.hashes.setdefault(call[1], {})
                bucket[call[2]] = bucket.get(call[2], 0) + call[3]
            else:
                self.redis.expiring[call[1]] = call[2]


class CounterTests(SimpleTestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.counter = load_counter.LoadCounter(client=lambda: self.redis)

    def test_counts_reach_redis_by_the_minute_of_the_flush(self):
        self.counter.record("http", 0.120)
        self.counter.record("http", 0.080)
        self.counter.record("bot", 0.010)
        self.counter.flush(now=600.0)  # minute 10
        self.assertEqual(
            load_counter.read_minute(10, client=self.redis),
            {"http": 2, "http_ms": 200, "bot": 1, "bot_ms": 10},
        )
        self.assertEqual(self.redis.expiring["botera:load:10"], load_counter.KEEP_SECONDS)
        # Flushed counts are not sent twice.
        self.counter.flush(now=601.0)
        self.assertEqual(load_counter.read_minute(10, client=self.redis)["http"], 2)

    def test_a_flush_at_most_every_ten_seconds(self):
        self.counter.flush(now=100.0)
        self.counter.record("http", 0.01)
        self.assertFalse(self.counter.due(now=105.0))
        self.assertTrue(self.counter.due(now=110.0))

    def test_redis_down_drops_the_counts_without_an_error(self):
        self.redis.broken = True
        self.counter.record("http", 0.05)
        self.counter.flush(now=60.0)
        self.assertEqual(load_counter.read_minute(1, client=self.redis), {})
        self.redis.broken = False
        self.counter.flush(now=61.0)
        self.assertEqual(load_counter.read_minute(1, client=self.redis), {})


class HttpMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.counter = load_counter.LoadCounter(client=lambda: FakeRedis())
        patcher = mock.patch.object(load_counter, "counter", self.counter)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_request(self, path):
        middleware = LoadCounterMiddleware(lambda request: "response")
        return middleware(RequestFactory().get(path))

    @override_settings(LOAD_COUNTER_ENABLED=True)
    def test_a_page_is_counted_static_files_and_health_are_not(self):
        self.assertEqual(self.run_request("/admin/"), "response")
        self.run_request("/static/unfold/js/app.js")
        self.run_request("/media/menu/1.webp")
        self.run_request("/health/")
        self.assertEqual(self.counter.pending()["http"], 1)

    @override_settings(LOAD_COUNTER_ENABLED=False)
    def test_off_in_tests_by_default(self):
        self.run_request("/admin/")
        self.assertEqual(self.counter.pending(), {})


class BotMiddlewareTests(SimpleTestCase):
    @override_settings(LOAD_COUNTER_ENABLED=True)
    def test_every_update_is_counted_and_handled(self):
        from bot.middlewares.load_counter import LoadCounterMiddleware as BotCounter

        counter = load_counter.LoadCounter(client=lambda: FakeRedis())
        with mock.patch.object(load_counter, "counter", counter):
            async def handler(event, data):
                return "handled"

            result = asyncio.run(BotCounter()(handler, object(), {}))
        self.assertEqual(result, "handled")
        self.assertEqual(counter.pending()["bot"], 1)

    def test_the_dispatcher_counts_updates(self):
        from bot.main import create_dispatcher
        from bot.middlewares.load_counter import LoadCounterMiddleware as BotCounter

        # The routers are attached once per process (another test may have).
        with mock.patch("aiogram.Dispatcher.include_router"):
            middlewares = create_dispatcher().update.outer_middleware
        self.assertTrue(any(isinstance(item, BotCounter) for item in middlewares))
