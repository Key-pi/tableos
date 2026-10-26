"""One line per run: load, response times, CPU, memory, queries, notifications.

usage: python summary.py [RUN ...]   (default: every run in results/)
Columns: partners, rate, actions/s, orders, p50/p95/p99/max of bot answers
(s), timeouts | mean CPU % of bot/worker/postgres/redis/loadgen and the whole
machine | peak memory MB of bot/worker/postgres | SQL queries per Telegram
update and mean ms per query | p95 of the new-order notification (s).
"""

import json
import pathlib
import statistics
import sys

RESULTS = pathlib.Path(__file__).with_name("results")


def _mean_cpu(samples, name):
    values = [row[name]["cpu"] for row in samples]
    return round(statistics.mean(values)) if values else 0


def _peak_rss(samples, name):
    return round(max((row[name]["rss_mb"] for row in samples), default=0))


def _queries(name, run):
    path = RESULTS / f"{name}.pg.txt"
    if not path.exists() or path.read_text().count("|") != 2 or not run.get("updates_total"):
        return "-"
    calls, _total_ms, mean_ms = path.read_text().strip().split("|")
    return f"{int(calls) / run['updates_total']:.1f} {mean_ms}"


def line(name: str) -> str | None:
    try:
        run = json.loads((RESULTS / f"{name}.json").read_text())
    except FileNotFoundError:
        return None
    cpu_path = RESULTS / f"{name}.cpu.json"
    samples = json.loads(cpu_path.read_text()) if cpu_path.exists() else []
    samples = samples[len(samples) // 4 :]  # skip start-up
    machine = round(statistics.mean(row["sys_cpu"] for row in samples)) if samples else 0
    answers = run["all"]
    cpu = "/".join(
        str(_mean_cpu(samples, n)) for n in ("bot", "worker", "postgres", "redis", "loadgen")
    )
    rss = "/".join(str(_peak_rss(samples, n)) for n in ("bot", "worker", "postgres"))
    notify = run.get("notify_new_order", {}).get("p95")
    return (
        f"{name:16} {run['partners']:>4} {run['rate']:>4} {run['actions_per_s']:>6} "
        f"{run['orders']:>6} {answers['p50']:>6} {answers['p95']:>6} {answers['p99']:>6} "
        f"{answers['max']:>6} {sum(run['timeouts'].values()):>4} | {cpu} {machine} | {rss} | "
        f"{_queries(name, run)} | {notify}"
    )


def main(names):
    names = names or sorted(
        p.stem for p in RESULTS.glob("*.json") if "." not in p.stem and "-u" not in p.stem
    )
    print(
        f"{'run':16} {'P':>4} {'rate':>4} {'act/s':>6} {'orders':>6} {'p50':>6} {'p95':>6} "
        f"{'p99':>6} {'max':>6} {'tmo':>4} | cpu% bot/wrk/pg/redis/gen sys | "
        "rss MB bot/wrk/pg | q/upd ms/q | notify p95"
    )
    for name in names:
        text = line(name)
        if text:
            print(text)


if __name__ == "__main__":
    main(sys.argv[1:])
