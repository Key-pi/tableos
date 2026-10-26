"""Concurrent Admin users against gunicorn (production settings).

usage: python admin_load.py --users 10 --duration 60 --url http://127.0.0.1:8000
Each virtual owner opens typical pages (orders, bills, order card, menu,
daily metrics) one after another. Prints p50/p95 per page and requests/s.
"""

import argparse
import asyncio
import json
import random
import statistics
import time
from collections import defaultdict

import aiohttp

PAGES = [
    "/admin/",
    "/admin/orders/order/",
    "/admin/billing/bill/",
    "/admin/menu/menuitem/",
    "/admin/orders/order/?status__exact=new",
    "/admin/users/guestprofile/",
    "/admin/analytics/partnerdailymetric/",
]


async def user(session, base, cookies, stop, lat, errors, detail_paths):
    headers = {"Host": "shop.example.com", "X-Forwarded-Proto": "https"}
    while not stop.is_set():
        path = random.choice(PAGES + detail_paths)
        start = time.monotonic()
        try:
            async with session.get(base + path, headers=headers, cookies=cookies,
                                   allow_redirects=False) as response:
                await response.read()
                key = path.split("?")[0]
                key = "/admin/orders/order/<id>/change/" if "/change/" in key else key
                if response.status != 200:
                    errors[f"{key} {response.status}"] += 1
                lat[key].append(time.monotonic() - start)
        except Exception as exc:  # noqa: BLE001
            errors[type(exc).__name__] += 1
        await asyncio.sleep(random.uniform(0.5, 2.0))


async def main(args):
    sessions = json.load(open(args.sessions))
    lat, errors = defaultdict(list), defaultdict(int)
    stop = asyncio.Event()
    async with aiohttp.ClientSession() as session:
        tasks = [
            asyncio.create_task(user(session, args.url, {"sessionid": s["sessionid"]}, stop,
                                     lat, errors, s["detail_paths"]))
            for s in (sessions * ((args.users // len(sessions)) + 1))[: args.users]
        ]
        await asyncio.sleep(args.duration)
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    total = sum(len(v) for v in lat.values())
    out = {"users": args.users, "rps": round(total / args.duration, 2), "errors": dict(errors),
           "pages": {}}
    for key, xs in sorted(lat.items()):
        xs.sort()
        out["pages"][key] = {"n": len(xs), "p50": round(statistics.median(xs), 3),
                             "p95": round(xs[int(0.95 * (len(xs) - 1))], 3)}
    everything = sorted(x for xs in lat.values() for x in xs)
    out["all"] = {"p50": round(statistics.median(everything), 3),
                  "p95": round(everything[int(0.95 * (len(everything) - 1))], 3)}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, default=10)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--sessions", default="admin_sessions.json")
    asyncio.run(main(parser.parse_args()))
