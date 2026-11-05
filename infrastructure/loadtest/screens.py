"""Open staff screens of the Mini App during a load run (PQ-048).

Each screen asks ``/app/<bot>/api/staff/state`` every INTERVAL seconds, as
``webapp/static/webapp/staff.js`` does: the first time in full, then with
``?since=<version>`` (a short answer while nothing changed). Two screens a
partner by default: the owner's and the cashier's phones.

usage (inside run.sh, with env.sh loaded):
    python screens.py --partners 50 --per-point 2 --interval 8 \
        --duration 150 --warmup 60 --base http://127.0.0.1:8000 --out results/x.screens.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import random
import statistics
import sys
import time

import aiohttp

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import django  # noqa: E402

django.setup()

from apps.partners.models import BotInstance  # noqa: E402
from webapp.auth import sign_init_data  # noqa: E402

PARTNERS = json.loads(pathlib.Path(__file__).with_name("partners.json").read_text())["partners"]
ROLES = ("owner", "manager", "cashier", "waiter")
PEOPLE = ("owner", "cashier", "manager", "waiter")  # who opens a screen first


def screens(count: int, per_point: int) -> list[tuple[str, str]]:
    """(url, initData) of every screen."""

    result = []
    for partner in PARTNERS[:count]:
        bot = BotInstance.objects.get(partner_id=partner["partner_id"])
        for role in PEOPLE[:per_point]:
            telegram_id = partner["staff"][role]
            init = sign_init_data(
                {"auth_date": str(int(time.time())),
                 "user": {"id": telegram_id, "first_name": role, "language_code": "uk"}},
                bot.token,
            )
            result.append((f"/app/{bot.id.hex}/api/staff/state", init))
    return result


async def screen(session, base, url, init, interval, stop_at, measure_from, stats, full):
    version = ""
    await asyncio.sleep(random.uniform(0, interval))  # screens are not in step
    while time.monotonic() < stop_at:
        started = time.monotonic()
        params = {"since": version} if version and not full else {}
        try:
            async with session.get(
                base + url, params=params,
                headers={"X-Telegram-Init-Data": init, "Host": "shop.example.com",
                         "X-Forwarded-Proto": "https"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                body = await response.json(content_type=None)
                ok = response.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            ok, body = False, {}
        took = time.monotonic() - started
        if started >= measure_from:
            stats["times"].append(took)
            if not ok:
                stats["errors"] += 1
            elif body.get("unchanged"):
                stats["unchanged"] += 1
            else:
                stats["full"] += 1
        if ok:
            version = body.get("version", version)
        await asyncio.sleep(max(0.0, interval - took))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--partners", type=int, required=True)
    parser.add_argument("--per-point", type=int, default=2)
    parser.add_argument("--interval", type=float, default=8)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--warmup", type=float, default=60)
    parser.add_argument("--startup", type=float, default=20)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--always-full", action="store_true",
                        help="never send since= (the screen before PQ-048 short answers)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    targets = screens(args.partners, args.per_point)  # the database, before the loop
    asyncio.run(run(args, targets))


async def run(args, targets) -> None:
    await asyncio.sleep(args.startup)
    measure_from = time.monotonic() + args.warmup
    stop_at = measure_from + args.duration
    stats = {"times": [], "full": 0, "unchanged": 0, "errors": 0}
    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=connector) as session:
        await asyncio.gather(*(
            screen(session, args.base, url, init, args.interval, stop_at, measure_from, stats,
                   args.always_full)
            for url, init in targets
        ))
    times = sorted(stats["times"])

    def pct(share):
        return round(times[min(len(times) - 1, int(len(times) * share))], 3) if times else None

    result = {
        "screens": len(targets), "interval": args.interval, "always_full": args.always_full,
        "requests_per_s": round(len(times) / args.duration, 1),
        "full": stats["full"], "unchanged": stats["unchanged"], "errors": stats["errors"],
        "p50": pct(0.5), "p95": pct(0.95), "p99": pct(0.99),
        "mean": round(statistics.mean(times), 3) if times else None,
    }
    pathlib.Path(args.out).write_text(json.dumps(result, indent=1))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
