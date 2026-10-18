#!/usr/bin/env bash
# Провокация дублей: QoS 1 и 2, потери 20 %, два провала связи по 12 с (> 1,5 × keep-alive 5 с).
#   bash tools/run_dup.sh | tee results/console/dup.txt
set -u
export MSYS_NO_PATHCONV=1
mkdir -p results/qos_dup
for s in 1 2 3; do
  for q in 1 2; do
    run="dupc_q${q}_s${s}"
    docker exec -d lab3-backend sh -c "python tools/qos_sink.py --run $run --qos $q --n 1000 --idle 20 > results/qos/${run}_sink.txt 2>&1"
    until [ -f "results/qos/${run}_ready" ]; do sleep 0.5; done
    docker exec lab3-device python tools/qos_pub.py --run "$run" --qos "$q" --loss 20 --n 1000 --inflight 20 --outage 12
    until [ -f "results/qos/${run}_sink.json" ]; do sleep 1; done
    cat "results/qos/${run}_sink.txt"
    mv results/qos/${run}_* results/qos_dup/
  done
done
