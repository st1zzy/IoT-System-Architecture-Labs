#!/usr/bin/env bash
# Тихие потери QoS 2: окно клиента 20 и 50 (MQTT 3.1.1), 50 (MQTT 5); 5 % потерь, без разрывов.
# В прогоне «50» — два независимых подписчика: одинаковые пропуски = брокер не принял сообщения.
#   bash tools/run_window.sh | tee results/console/window.txt
set -u
export MSYS_NO_PATHCONV=1
D=results/qos_window; mkdir -p $D
for cfg in "w20_v311:20:" "w50_v311:50:" "w50_v5:50:--v5"; do
  IFS=: read -r name win flag <<< "$cfg"
  run="win_${name}"
  docker exec -d lab3-backend sh -c "python tools/qos_sink.py --run $run --qos 2 --n 1000 --idle 15 > results/qos/${run}_sink.txt 2>&1"
  [ "$name" = w50_v311 ] && docker exec -d lab3-backend sh -c "python tools/qos_sink.py --run ${run}_B --topic-run $run --qos 2 --n 1000 --idle 15 > results/qos/${run}_B_sink.txt 2>&1"
  until [ -f "results/qos/${run}_ready" ]; do sleep 0.5; done; sleep 1
  docker exec lab3-device python tools/qos_pub.py --run "$run" --qos 2 --loss 5 --n 1000 --inflight "$win" $flag
  until [ -f "results/qos/${run}_sink.json" ]; do sleep 1; done
  [ "$name" = w50_v311 ] && until [ -f "results/qos/${run}_B_sink.json" ]; do sleep 1; done
  cat results/qos/${run}_sink.txt
  [ "$name" = w50_v311 ] && cat results/qos/${run}_B_sink.txt && uv run --no-project -q python - "$run" <<'PY'
import json, sys
r = sys.argv[1]
a = set(json.load(open(f"results/qos/{r}_sink.json"))["missing"])
b = set(json.load(open(f"results/qos/{r}_B_sink.json"))["missing"])
print(f"два подписчика: пропущено {len(a)} и {len(b)}, из них общих {len(a & b)}: {sorted(a & b)[:15]}…")
PY
  mv results/qos/${run}* $D/
done
