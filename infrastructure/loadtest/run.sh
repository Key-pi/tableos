#!/bin/bash
# One load run: fake Telegram + virtual guests and staff, the bot, the workers;
# samples CPU and memory, counts SQL queries, then stops everything.
#
# usage: run.sh PARTNERS RATE DURATION WARMUP OUT [STACK_CPUS] [GEN_CPUS]
#   RATE      peak load per partner, 1 = about 100 guests an hour
#   OUT       results/OUT.json (+ .cpu.json, .pg.txt, logs)
#   STACK_CPUS / GEN_CPUS  taskset CPU lists for the stack and the generator
# environment:
#   BOT_PROCESSES     runbot --processes N (default 1)
#   WORKER_CONC       notification worker processes (default 2)
#   BROADCAST_WORKER  1 = separate broadcasts worker, as in docker compose
#   WORKER_QUEUES     queues of the notification worker (default "default")
#   API_DELAY         seconds added to every Telegram call (e.g. 0.04)
#   SCREENS           open Mini App staff screens per partner (default 0); with
#                     them the site runs too (gunicorn, WEB_WORKERS, default 3)
#                     and results/OUT.screens.json has their answer times;
#                     SCREENS_FULL=1 — screens never ask "since" (full answers)
#   KEEP_OPEN_ORDERS  1 = keep orders left open by earlier runs (by default they
#                     are closed first: a venue does not carry hundreds of open
#                     orders, and the staff lists would measure that instead)
#   REPO              code to run (default: this checkout)
set -u
L=$(cd "$(dirname "$0")" && pwd)
. "$L/env.sh"
P=$1; RATE=$2; DUR=$3; WARM=$4; OUT=$5; STACK=${6:-0-1}; GEN=${7:-0-1}
mkdir -p "$L/results"
R=$L/results

cd "$L"
taskset -c "$GEN" python3 loadgen.py --partners "$P" --rate "$RATE" --duration "$DUR" \
  --warmup "$WARM" --startup 20 --api-delay "${API_DELAY:-0}" --out "$R/$OUT.json" \
  > "$R/$OUT.loadgen.log" 2>&1 &
GENPID=$!
sleep 2

cd "$REPO"
# Only the first P load partners' bots run.
python3 - <<PY
import django; django.setup()
from apps.partners.models import BotInstance
ids = list(BotInstance.objects.filter(partner__slug__startswith="lt-")
           .order_by("partner__slug").values_list("id", flat=True))
BotInstance.objects.filter(id__in=ids[:$P]).update(is_active=True)
BotInstance.objects.filter(id__in=ids[$P:]).update(is_active=False)
PY

if [ "${KEEP_OPEN_ORDERS:-0}" != 1 ]; then
  # Stand only: the throwaway database, not a way to fix real data.
  psql -qc "update orders_order set status='completed' where status not in
    ('completed','canceled') and partner_id in (select id from partners_partner
    where slug like 'lt-%')" > /dev/null
fi

PROC_ARGS=""
if [ "${BOT_PROCESSES:-1}" -gt 1 ]; then PROC_ARGS="--processes $BOT_PROCESSES"; fi
taskset -c "$STACK" python3 manage.py runbot $PROC_ARGS > "$R/$OUT.bot.log" 2>&1 &
BOTPID=$!
taskset -c "$STACK" celery -A core.celery worker -Q "${WORKER_QUEUES:-default}" \
  --concurrency "${WORKER_CONC:-2}" --loglevel=warning > "$R/$OUT.worker.log" 2>&1 &
WPID=$!
if [ "${BROADCAST_WORKER:-1}" = 1 ]; then
  taskset -c "$STACK" celery -A core.celery worker -Q broadcasts --pool=solo \
    --hostname=broadcasts@%h --loglevel=warning > "$R/$OUT.broadcasts.log" 2>&1 &
fi
if [ "${SCREENS:-0}" -gt 0 ]; then
  # As in production: gunicorn with 3 workers behind the proxy (PQ-048).
  python3 manage.py shell -c "from apps.partners.models import PartnerBotSettings as S; \
S.objects.filter(partner__slug__startswith='lt-').update(module_webapp_enabled=True)" > /dev/null
  taskset -c "$STACK" gunicorn core.wsgi:application --bind 127.0.0.1:8000 \
    --workers "${WEB_WORKERS:-3}" --timeout 60 > "$R/$OUT.web.log" 2>&1 &
  WEBPID=$!
  taskset -c "$GEN" python3 "$L/screens.py" --partners "$P" --per-point "$SCREENS" \
    --duration "$DUR" --warmup "$WARM" --startup 18 --out "$R/$OUT.screens.json" ${SCREENS_FULL:+--always-full} \
    > "$R/$OUT.screens.log" 2>&1 &
fi
for pid in $(pgrep -x postgres) $(pgrep -x redis-server); do
  taskset -pc "$STACK" "$pid" > /dev/null 2>&1 || true
done
psql -qc "select pg_stat_statements_reset()" > /dev/null 2>&1 || true
python3 "$L/sample.py" "$BOTPID" "$WPID" "$GENPID" > "$R/$OUT.cpu.json" &
SPID=$!

wait $GENPID
kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
psql -At -F'|' -c "select sum(calls), round(sum(total_exec_time)::numeric,1),
  round((sum(total_exec_time)/nullif(sum(calls),0))::numeric,3) from pg_stat_statements
  where dbid=(select oid from pg_database where datname=current_database())" \
  > "$R/$OUT.pg.txt" 2>/dev/null || true
kill $BOTPID
if [ -n "${WEBPID:-}" ]; then sleep 3; kill $WEBPID; fi
pkill -TERM -f "celery -A core.celery worker"
sleep 8
tail -2 "$R/$OUT.loadgen.log"
