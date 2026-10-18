"""Сторона backend в эксперименте QoS: приём, задержка, дубли и пропуски по seq.

Подписка с тем же QoS, persistent-сессия; завершение — после тишины idle секунд.
    python tools/qos_sink.py --run q1_l20_s1 --qos 1 --n 1000
"""

import argparse
import json
import struct
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

V2 = mqtt.CallbackAPIVersion.VERSION2
OUT = Path("/lab/results/qos")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--qos", type=int, required=True)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--idle", type=float, default=20)
    ap.add_argument("--max-wait", type=float, default=180)
    ap.add_argument("--topic-run", help="подписаться на топик другого прогона (второй независимый подписчик)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    seen: dict[int, float] = {}
    dups, dup_flag = 0, 0
    last = [time.time()]

    def on_msg(_c, _u, m):
        nonlocal dups, dup_flag
        t = time.time()
        seq, ts = struct.unpack_from("!Id", m.payload)
        last[0] = t
        if m.dup:
            dup_flag += 1
        if seq in seen:
            dups += 1
        else:
            seen[seq] = (t - ts) * 1000

    c = mqtt.Client(V2, client_id=f"sink-{args.run}", clean_session=False)
    c.on_message = on_msg
    c.on_connect = lambda cl, *_: cl.subscribe(f"lab3/qos/{args.topic_run or args.run}", qos=args.qos)
    c.connect("lab3-mosquitto", 1883)
    c.loop_start()
    (OUT).mkdir(parents=True, exist_ok=True)
    (OUT / f"{args.run}_ready").write_text("1")
    t0 = time.time()
    while time.time() - t0 < args.max_wait:
        if seen and time.time() - last[0] > args.idle:
            break
        time.sleep(0.5)
    c.loop_stop()
    c.disconnect()
    lat = [seen[s] for s in sorted(seen)]
    res = {"run": args.run, "qos": args.qos, "n": args.n, "delivered": len(seen), "dups": dups,
           "dup_flag": dup_flag, "latency_ms": lat,
           "missing": sorted(set(range(args.n)) - set(seen))}
    (OUT / f"{args.run}_sink.json").write_text(json.dumps(res), encoding="utf-8")
    lat_s = sorted(lat)
    p = lambda q: lat_s[min(len(lat_s) - 1, int(q * (len(lat_s) - 1)))] if lat_s else float("nan")  # noqa: E731
    print(f"sink {args.run}: доставлено {len(seen)}/{args.n}, дублей {dups} (с флагом DUP {dup_flag}), "
          f"задержка p50 {p(0.5):.0f} мс, p99 {p(0.99):.0f} мс, среднее {sum(lat) / max(1, len(lat)):.0f} мс")


if __name__ == "__main__":
    main()
