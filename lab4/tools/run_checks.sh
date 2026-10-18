#!/usr/bin/env bash
# Все проверки Lab 4 с хоста: checks.py в клиенте + рестарт брокера A для проверки 3 + вывод EMQX.
#   bash tools/run_checks.sh
set -u
export MSYS_NO_PATHCONV=1
C=results/console; mkdir -p $C
rm -f results/need_restart results/restarted
docker exec lab4-client python tools/checks.py > $C/checks.txt 2>&1 &
PID=$!
until [ -f results/need_restart ] || ! kill -0 $PID 2> /dev/null; do sleep 0.5; done
if [ -f results/need_restart ]; then
  rm -f results/need_restart
  docker restart lab4-mosquitto-a > /dev/null && sleep 2 && echo 1 > results/restarted
fi
wait $PID
cat $C/checks.txt
{ docker exec lab4-emqx emqx ctl status; docker exec lab4-emqx emqx ctl cluster status;
  docker exec lab4-emqx emqx ctl listeners | grep -E 'default|acceptors|running' | head -12; } > $C/emqx.txt 2>&1
docker logs lab4-mosquitto-a 2>&1 | grep -E 'not authorised|Denied|dropped|bridge|exceeded|Saving|Restored|max_queued' | tail -25 > $C/broker_a.log
