#!/bin/bash
# A broadcast to GUESTS guests of partner lt-0000 in the middle of a peak run.
# usage: broadcast_under_load.sh PARTNERS GUESTS OUT
L=$(cd "$(dirname "$0")" && pwd)
. "$L/env.sh"
P=$1; GUESTS=$2; OUT=$3
"$L/run.sh" "$P" 1 180 60 "$OUT" > /dev/null 2>&1 &
RUN=$!
until [ -f "$L/results/$OUT.cpu.json" ]; do sleep 2; done
START=$(date +%s); until [ $(( $(date +%s) - START )) -ge 90 ]; do sleep 5; done
cd "$REPO" && python3 "$L/broadcast_test.py" "$GUESTS" | tail -1
wait $RUN
python3 "$L/summary.py" "$OUT"
