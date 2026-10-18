#!/usr/bin/env bash
# Обрыв связи: брокер останавливается на OUTAGE секунд, хронология — в timeline.txt.
#   bash tools/outage.sh results/main 90
set -euo pipefail
OUT=${1:-results/main}
OUTAGE=${2:-90}
mkdir -p "$OUT"
echo "$(date -u +%T) docker stop lab1-mosquitto"  | tee -a "$OUT/timeline.txt"
docker stop lab1-mosquitto > /dev/null
sleep "$OUTAGE"
echo "$(date -u +%T) docker start lab1-mosquitto" | tee -a "$OUT/timeline.txt"
docker start lab1-mosquitto > /dev/null
