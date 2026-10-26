#!/bin/bash
# Admin under concurrent owners: gunicorn with 3 workers as in production.
# usage: admin_run.sh OUT USERS...   (first: python make_admin_sessions.py K)
L=$(cd "$(dirname "$0")" && pwd)
. "$L/env.sh"
OUT=$1; shift
mkdir -p "$L/results"
cd "$REPO"
python3 manage.py collectstatic --noinput --verbosity 0
taskset -c 0-1 gunicorn core.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 60 \
  --forwarded-allow-ips '*' > "$L/results/$OUT.gunicorn.log" 2>&1 &
G=$!
until curl -s -o /dev/null 127.0.0.1:8000/; do sleep 0.5; done
cd "$L"
for u in "$@"; do
  python3 admin_load.py --users "$u" --duration 60 --sessions "$L/admin_sessions.json" \
    > "$L/results/$OUT-u$u.json"
  python3 -c "import json; d = json.load(open('$L/results/$OUT-u$u.json')); \
print('$OUT users=$u', 'rps', d['rps'], 'p50', d['all']['p50'], 'p95', d['all']['p95'], \
'errors', d['errors'])"
done
ps -o rss= --ppid $G | awk '{s += $1} END {print "gunicorn workers RSS MB", s / 1024}'
kill $G; wait $G 2>/dev/null
