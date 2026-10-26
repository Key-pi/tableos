"""Fake Telegram Bot API + virtual guests and staff for Botera Shop load tests.

One asyncio process: an aiohttp server answering the Bot API methods the bot
uses (getUpdates long-polling, sendMessage, editMessageText, ...), and virtual
users that inject updates and wait for the bot's answer, following the
keyboards the bot sends like a person would.

usage: python loadgen.py --partners 50 --rate 1.0 --duration 240 --warmup 60
  --rate: peak-hour load units per partner (1.0 = 100 guests/hour, 150
  orders/hour, staff handling them).
Writes a JSON summary to --out.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import pathlib
import random
import statistics
import time
from collections import defaultdict

from aiohttp import web

PORT = 8081
GUESTS_PER_HOUR = 100  # one load unit: a busy café at the peak hour
THINK = (3.0, 12.0)  # seconds between a guest's actions
STAFF_PERIOD = 3.0  # seconds between a staff member's actions at rate 1.0
ANSWER_TIMEOUT = 30.0


class Chat:
    """What one Telegram chat (a guest or staff member) has received."""

    def __init__(self):
        self.messages: dict[int, dict] = {}
        self.reply_keyboard: list[str] = []
        self.event = asyncio.Event()
        self.log: list[tuple[float, str, dict]] = []

    def buttons(self, prefix: str = "") -> list[str]:
        found = []
        for message in sorted(self.messages.values(), key=lambda m: -m["message_id"]):
            for row in (message.get("reply_markup") or {}).get("inline_keyboard", []):
                for button in row:
                    data = button.get("callback_data", "")
                    if data.startswith(prefix):
                        found.append((message["message_id"], data))
        return found


class FakeTelegram:
    def __init__(self, tokens):
        self.bots = {t: {"updates": [], "event": asyncio.Event(), "next": 1} for t in tokens}
        self.chats: dict[int, Chat] = defaultdict(Chat)
        self.callbacks: dict[str, asyncio.Future] = {}
        self.message_ids = itertools.count(1000)
        self.stats = defaultdict(int)
        self.bot_ids = {t: int(t.split(":")[0]) for t in tokens}
        self.pushed = 0
        self.api_delay = 0.0
        self.notify_waiters: dict[tuple[int, str], float] = {}
        self.notify_latency: list[float] = []

    # --- injection -----------------------------------------------------------
    def push(self, token, update):
        self.pushed += 1
        bot = self.bots[token]
        update["update_id"] = bot["next"]
        bot["next"] += 1
        bot["updates"].append(update)
        bot["event"].set()

    # --- HTTP ----------------------------------------------------------------
    async def handle(self, request):
        token = request.match_info["token"]
        method = request.match_info["method"].lower()
        data = dict(await request.post()) if request.can_read_body else {}
        data.update(request.query)
        self.stats[method] += 1
        if self.api_delay and method != "getupdates":
            await asyncio.sleep(self.api_delay)  # network round trip to Telegram
        result = await self.dispatch(token, method, data)
        return web.json_response({"ok": True, "result": result})

    def _bot_user(self, token):
        return {"id": self.bot_ids.get(token, 1), "is_bot": True, "first_name": "LT",
                "username": f"lt_bot_{self.bot_ids.get(token, 1)}"}

    async def dispatch(self, token, method, data):
        if method == "getme":
            return self._bot_user(token)
        if method == "getupdates":
            bot = self.bots.get(token)
            if bot is None:
                await asyncio.sleep(1)
                return []
            offset = int(data.get("offset") or 0)
            bot["updates"] = [u for u in bot["updates"] if u["update_id"] >= offset]
            if not bot["updates"]:
                bot["event"].clear()
                try:
                    await asyncio.wait_for(bot["event"].wait(), float(data.get("timeout") or 10))
                except asyncio.TimeoutError:
                    return []
            return bot["updates"][:100]
        chat_id = data.get("chat_id")
        if method == "answercallbackquery":
            future = self.callbacks.pop(data.get("callback_query_id"), None)
            if future is not None and not future.done():
                future.set_result(time.monotonic())
            return True
        if chat_id is None:
            return True
        chat = self.chats[int(chat_id)]
        markup = json.loads(data["reply_markup"]) if data.get("reply_markup") else None
        now = time.monotonic()
        if method in ("sendmessage", "sendphoto", "senddocument"):
            message_id = next(self.message_ids)
            message = {"message_id": message_id, "date": int(time.time()),
                       "chat": {"id": int(chat_id), "type": "private"},
                       "from": self._bot_user(token), "text": data.get("text", "")}
            if markup and "inline_keyboard" in markup:
                message["reply_markup"] = markup
            if markup and "keyboard" in markup:
                chat.reply_keyboard = [b["text"] for row in markup["keyboard"] for b in row]
            chat.messages[message_id] = message
            chat.log.append((now, method, message))
            text = data.get("text", "")
            for key, started in list(self.notify_waiters.items()):
                if key[0] == int(chat_id) and key[1] in text:
                    self.notify_latency.append(now - started)
                    del self.notify_waiters[key]
            chat.event.set()
            return message
        if method in ("editmessagetext", "editmessagereplymarkup"):
            message_id = int(data.get("message_id"))
            message = chat.messages.setdefault(
                message_id, {"message_id": message_id, "date": int(time.time()),
                             "chat": {"id": int(chat_id), "type": "private"},
                             "from": self._bot_user(token), "text": ""})
            if "text" in data:
                message["text"] = data["text"]
            if markup and "inline_keyboard" in markup:
                message["reply_markup"] = markup
            else:
                message.pop("reply_markup", None)
            chat.log.append((now, method, message))
            chat.event.set()
            return message
        if method == "deletemessage":
            chat.messages.pop(int(data.get("message_id", 0)), None)
            return True
        return True


class Metrics:
    def __init__(self):
        self.latency = defaultdict(list)
        self.timeouts = defaultdict(int)
        self.notify = []
        self.orders = 0
        self.actions_in_window = 0
        self.window_start = None

    def record(self, kind, seconds):
        if self.window_start is None:
            return
        self.latency[kind].append(seconds)
        self.actions_in_window += 1

    def miss(self, kind):
        if self.window_start is not None:
            self.timeouts[kind] += 1


class User:
    ids = itertools.count(1)

    def __init__(self, tg: FakeTelegram, token: str, chat_id: int, metrics: Metrics):
        self.tg, self.token, self.chat_id, self.m = tg, token, chat_id, metrics
        self.chat = tg.chats[chat_id]
        self.last_message_id = None

    def _from(self):
        return {"id": self.chat_id, "is_bot": False, "first_name": "Guest",
                "language_code": "uk"}

    async def text(self, kind, text):
        start = time.monotonic()
        before = len(self.chat.log)
        self.chat.event.clear()
        self.tg.push(self.token, {"message": {
            "message_id": next(self.tg.message_ids), "date": int(time.time()),
            "chat": {"id": self.chat_id, "type": "private"}, "from": self._from(),
            "text": text}})
        deadline = start + ANSWER_TIMEOUT
        while len(self.chat.log) == before:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.m.miss(kind)
                return False
            self.chat.event.clear()
            try:
                await asyncio.wait_for(self.chat.event.wait(), remaining)
            except asyncio.TimeoutError:
                pass
        self.m.record(kind, time.monotonic() - start)
        await asyncio.sleep(0.2)  # let follow-up messages (keyboards) arrive
        return True

    async def press(self, kind, message_id, data):
        start = time.monotonic()
        callback_id = str(next(User.ids))
        future = asyncio.get_running_loop().create_future()
        self.tg.callbacks[callback_id] = future
        message = self.chat.messages.get(message_id) or {
            "message_id": message_id, "date": int(time.time()),
            "chat": {"id": self.chat_id, "type": "private"}, "text": ""}
        self.tg.push(self.token, {"callback_query": {
            "id": callback_id, "from": self._from(), "chat_instance": str(self.chat_id),
            "data": data, "message": {**message, "from": self.tg._bot_user(self.token)}}})
        try:
            await asyncio.wait_for(future, ANSWER_TIMEOUT)
        except asyncio.TimeoutError:
            self.tg.callbacks.pop(callback_id, None)
            self.m.miss(kind)
            return False
        self.m.record(kind, time.monotonic() - start)
        return True

    async def wait_buttons(self, prefix, timeout=3.0):
        deadline = time.monotonic() + timeout
        while True:
            found = self.chat.buttons(prefix)
            if found or time.monotonic() > deadline:
                return found
            await asyncio.sleep(0.05)


async def think(scale):
    await asyncio.sleep(random.uniform(*THINK) / scale)


async def guest_session(tg, partner, labels, metrics, chat_id, scale):
    u = User(tg, partner["token"], chat_id, metrics)
    payload = random.choice(partner["tables"])
    if not await u.text("start_qr", f"/start {payload}"):
        return
    choice = await u.wait_buttons("tv:", timeout=0.5)
    if choice:
        message_id, data = random.choice(choice)
        await u.press("visit_choice", message_id, data)
    for _round in range(2 if random.random() < 0.5 else 1):
        await think(scale)
        if not await u.text("menu", labels["menu"]):
            return
        for _ in range(2):
            cats = await u.wait_buttons("menucat:")
            if not cats:
                break
            await think(scale)
            message_id, data = random.choice(cats)
            if not await u.press("menu_category", message_id, data):
                return
            items = await u.wait_buttons("menuadd:")
            for _add in range(random.randint(1, 2)):
                if not items:
                    break
                await think(scale)
                message_id, data = random.choice(items)
                await u.press("add_to_cart", message_id, data)
        await think(scale)
        if not await u.text("cart", labels["cart"]):
            return
        checkout = await u.wait_buttons("cart:checkout")
        if checkout:
            await think(scale)
            pressed_at = time.monotonic()
            if await u.press("checkout", *checkout[0]):
                metrics.orders += 1
                import re as _re
                for message in sorted(u.chat.messages.values(), key=lambda m: -m["message_id"]):
                    found = _re.search(r"#([0-9a-f]{8})", message.get("text", ""))
                    if found:
                        if metrics.window_start is not None:
                            owner = partner["staff"]["owner"]
                            # The notification may arrive before the bot answers the guest.
                            early = [t for t, _m, msg in tg.chats[owner].log
                                     if t >= pressed_at and found.group(1) in msg.get("text", "")]
                            if early:
                                tg.notify_latency.append(early[0] - pressed_at)
                            else:
                                tg.notify_waiters[(owner, found.group(1))] = pressed_at
                        break
    await think(scale)
    if await u.text("request_bill", labels["request_bill"]):
        options = await u.wait_buttons("billreq:personal")
        if options:
            await u.press("bill_personal", *options[0])


STAFF_PRIORITY = (
    "staffbillpaycash:", "staffbillissue:", "stafforder:", "stafftxok:",
)


async def staff_loop(tg, partner, metrics, scale, stop):
    u = User(tg, partner["token"], partner["staff"]["cashier"], metrics)
    await u.text("staff_start", "/staff")
    while not stop.is_set():
        await asyncio.sleep(random.uniform(0.5, 1.5) * STAFF_PERIOD / scale)
        refresh = await u.wait_buttons("stafforders:refresh", 1)
        if refresh and random.random() < 0.7:
            await u.press("staff_orders", *refresh[0])
            opened = await u.wait_buttons("stafforderopen:", 1)
            if opened:
                await asyncio.sleep(random.uniform(0.5, 1.5) * STAFF_PERIOD / scale)
                await u.press("staff_open_order", *random.choice(opened[:3]))
                actions = [b for b in await u.wait_buttons("stafforder:", 1)
                           if not b[1].endswith(":canceled")]
                if actions:
                    await asyncio.sleep(random.uniform(0.5, 1.5) * STAFF_PERIOD / scale)
                    await u.press("staff_order_status", *actions[0])
        else:
            bills = await u.wait_buttons("staffbill:list", 1)
            if bills:
                await u.press("staff_bills", *bills[0])
                opened = await u.wait_buttons("staffbillopen:", 1)
                if opened:
                    await asyncio.sleep(random.uniform(0.5, 1.5) * STAFF_PERIOD / scale)
                    await u.press("staff_open_bill", *random.choice(opened[:3]))
                    for prefix in STAFF_PRIORITY[:2]:
                        found = await u.wait_buttons(prefix, 0.5)
                        if found:
                            await u.press("staff_bill_action", *found[0])
                            break
        # back to the main staff screen now and then (fresh keyboards)
        if random.random() < 0.15:
            await u.text("staff_start", "/staff")


async def partner_load(tg, partner, labels, metrics, rate, scale, stop, chat_ids):
    tasks = [asyncio.create_task(staff_loop(tg, partner, metrics, rate, stop))]
    # Poisson arrivals of guests: GUESTS_PER_HOUR * rate per hour.
    per_second = GUESTS_PER_HOUR * rate / 3600
    while not stop.is_set():
        await asyncio.sleep(random.expovariate(per_second))
        if stop.is_set():
            break
        tasks.append(asyncio.create_task(
            guest_session(tg, partner, labels, metrics, next(chat_ids), scale)))
    for task in tasks:
        task.cancel()


def summarize(metrics, seconds):
    out = {"window_s": seconds, "actions": metrics.actions_in_window,
           "actions_per_s": round(metrics.actions_in_window / seconds, 2),
           "orders": metrics.orders, "timeouts": dict(metrics.timeouts), "by_action": {}}
    everything = [x for xs in metrics.latency.values() for x in xs]

    def pct(xs, p):
        if not xs:
            return None
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(p / 100 * len(xs)))], 3)

    out["all"] = {"n": len(everything), "p50": pct(everything, 50), "p95": pct(everything, 95),
                  "p99": pct(everything, 99), "max": pct(everything, 100)}
    for kind, xs in sorted(metrics.latency.items()):
        out["by_action"][kind] = {"n": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95),
                                  "mean": round(statistics.mean(xs), 3)}
    return out


async def main(args):
    data = json.load(open(args.data))
    partners = data["partners"][: args.partners]
    labels = data["labels"]
    tg = FakeTelegram([p["token"] for p in data["partners"]])
    app = web.Application(client_max_size=10 * 1024 * 1024)
    app.router.add_route("*", "/bot{token}/{method}", tg.handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    print("fake telegram on", PORT, flush=True)
    tg.api_delay = args.api_delay
    if args.serve_only:
        await asyncio.Event().wait()
    await asyncio.sleep(args.startup)  # let runbot connect
    run_started = time.monotonic()
    metrics = Metrics()
    stop = asyncio.Event()
    chat_ids = itertools.count(500000000)
    tasks = [asyncio.create_task(partner_load(tg, p, labels, metrics, args.rate,
                                              1.0, stop, chat_ids)) for p in partners]
    await asyncio.sleep(args.warmup)
    metrics.window_start = time.monotonic()
    started = time.monotonic()
    await asyncio.sleep(args.duration)
    window = time.monotonic() - started
    summary = summarize(metrics, window)
    metrics.window_start = None
    summary["partners"] = len(partners)
    summary["rate"] = args.rate
    summary["telegram_calls"] = dict(tg.stats)
    summary["updates_total"] = tg.pushed
    summary["run_seconds"] = round(time.monotonic() - run_started, 1)
    xs = sorted(tg.notify_latency)
    summary["notify_new_order"] = {
        "n": len(xs), "waiting": len(tg.notify_waiters),
        "p50": round(xs[len(xs) // 2], 3) if xs else None,
        "p95": round(xs[int(0.95 * (len(xs) - 1))], 3) if xs else None,
    }
    stop.set()
    for task in tasks:
        task.cancel()
    json.dump(summary, open(args.out, "w"), indent=1)
    print(json.dumps(summary["all"]), summary["actions_per_s"], flush=True)
    await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(pathlib.Path(__file__).with_name("partners.json")))
    parser.add_argument("--partners", type=int, default=10)
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=240)
    parser.add_argument("--warmup", type=float, default=60)
    parser.add_argument("--startup", type=float, default=15)
    parser.add_argument("--out", default="result.json")
    parser.add_argument("--serve-only", action="store_true")
    parser.add_argument("--api-delay", type=float, default=0.0)
    asyncio.run(main(parser.parse_args()))
