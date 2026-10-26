#!/bin/bash
# usage: series.sh STACK_CPUS GEN_CPUS PREFIX "P:RATE P:RATE ..."
L=$(cd "$(dirname "$0")" && pwd)
for pr in $4; do
  P=${pr%%:*}; R=${pr##*:}
  $L/run.sh $P $R ${DUR:-180} ${WARM:-60} "$3-p${P}-r${R}" $1 $2 > /dev/null 2>&1
  python3 $L/summary.py "$3-p${P}-r${R}" | tail -1
done
