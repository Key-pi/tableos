"""Sample CPU % and memory of the stack every 2 s until SIGTERM; print JSON.

usage: python sample.py BOT_PID WORKER_PID LOADGEN_PID
Groups: bot (with its child processes), worker (every Celery worker process),
postgres, redis, loadgen; plus the whole machine (sys_cpu).
"""

import json
import signal
import sys
import time

import psutil

bot = psutil.Process(int(sys.argv[1]))
generator = psutil.Process(int(sys.argv[3]))
NAMES = ("bot", "worker", "postgres", "redis", "loadgen")
_known: dict[int, psutil.Process] = {}
running = True


def stop(*_unused):
    global running
    running = False


def _same(process: psutil.Process) -> psutil.Process:
    # One object per pid, so cpu_percent() compares with its last reading.
    return _known.setdefault(process.pid, process)


def _group(name: str) -> list[psutil.Process]:
    if name == "bot":
        try:
            return [bot, *bot.children(recursive=True)]
        except psutil.Error:
            return []
    if name == "loadgen":
        return [generator]
    if name == "worker":
        found = []
        for process in psutil.process_iter(["cmdline"]):
            command = " ".join(process.info["cmdline"] or [])
            if "celery" in command and " worker" in command:
                found.append(process)
        return found
    executable = {"postgres": "postgres", "redis": "redis-server"}[name]
    return [p for p in psutil.process_iter(["name"]) if p.info["name"] == executable]


def group(name: str) -> list[psutil.Process]:
    return [_same(process) for process in _group(name)]


signal.signal(signal.SIGTERM, stop)
for name in NAMES:
    for process in group(name):
        try:
            process.cpu_percent(None)
        except psutil.Error:
            pass
psutil.cpu_percent(None)

samples = []
while running:
    time.sleep(2)
    row = {"t": time.time()}
    for name in NAMES:
        cpu = rss = 0.0
        for process in group(name):
            try:
                cpu += process.cpu_percent(None)
                rss += process.memory_info().rss
            except psutil.Error:
                pass
        row[name] = {"cpu": round(cpu, 1), "rss_mb": round(rss / 2**20, 1)}
    row["sys_cpu"] = psutil.cpu_percent(None)
    samples.append(row)
print(json.dumps(samples))
