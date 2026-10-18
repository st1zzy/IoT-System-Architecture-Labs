#!/usr/bin/env bash
# Эксперимент «брокер недоступен»: остановка mosquitto на OUTAGE секунд во время потока.
# Наблюдатель держит persistent-сессию, поэтому видит и сообщения, досланные из буфера.
#   bash tools/broker_outage.sh results/outage 30
set -euo pipefail
OUT=${1:-results/outage}
OUTAGE=${2:-30}
PY=${PY:-python}
mkdir -p "$OUT"

since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
$PY tools/measure.py --persistent --duration $((OUTAGE + 60)) --out "$OUT/measure.json" > /dev/null &
mon=$!

sleep 15
echo "$(date -u +%T) docker stop mosquitto"  | tee    "$OUT/timeline.txt"
docker stop mosquitto > /dev/null
sleep "$OUTAGE"
echo "$(date -u +%T) docker start mosquitto" | tee -a "$OUT/timeline.txt"
docker start mosquitto > /dev/null

wait $mon
docker compose logs --no-log-prefix --since "$since" simulator > "$OUT/simulator.log"
cat "$OUT/measure.json"
