#!/usr/bin/env bash
# Эксперимент QoS × потери: 3 серии × QoS 0/1/2 × потери 0/5/20 % по 1000 сообщений.
# Окно in-flight клиента 20 = max_inflight_messages брокера (см. отчёт: при 50 QoS 2 теряет сообщения).
#   bash tools/run_qos.sh | tee results/console/qos.txt
set -u
export MSYS_NO_PATHCONV=1
for s in 1 2 3; do
  for loss in 0 5 20; do
    for q in 0 1 2; do
      run="q${q}_l${loss}_s${s}"
      docker exec -d lab3-backend sh -c "python tools/qos_sink.py --run $run --qos $q --n 1000 --idle 15 > results/qos/${run}_sink.txt 2>&1"
      until [ -f "results/qos/${run}_ready" ]; do sleep 0.5; done
      docker exec lab3-device python tools/qos_pub.py --run "$run" --qos "$q" --loss "$loss" --n 1000 --inflight 20
      until [ -f "results/qos/${run}_sink.json" ]; do sleep 1; done
      cat "results/qos/${run}_sink.txt"
    done
  done
done
