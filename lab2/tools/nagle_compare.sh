#!/usr/bin/env bash
# Время подключения TLS 1.2 / 1.3 при настройке брокера по умолчанию и с set_tcp_nodelay true.
#   bash tools/nagle_compare.sh
set -euo pipefail
for cfg in nagle nodelay; do
  conf=mosquitto.conf; [ "$cfg" = nagle ] && conf=mosquitto-nagle.conf
  MOSQ_CONF=$conf docker compose up -d --force-recreate --wait mosquitto > /dev/null 2>&1
  docker exec lab2-client python tools/run_profiles.py --connect-only "$cfg" --profiles d01,d20 --n 30
done
