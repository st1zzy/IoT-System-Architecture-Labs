"""Пассивный наблюдатель MQTT-потока: интервал, размер, задержка, непрерывность seq.

Пример:
    python tools/measure.py --duration 120 --out results/run1.json
"""

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict

import paho.mqtt.client as mqtt


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def mqtt_publish_size(topic: str, payload_len: int, qos: int) -> int:
    """Полный размер PUBLISH-пакета MQTT 3.1.1 (без TCP/IP)."""
    remaining = 2 + len(topic.encode()) + (2 if qos > 0 else 0) + payload_len
    rl_bytes = 1 if remaining < 128 else 2 if remaining < 16384 else 3
    return 1 + rl_bytes + remaining


def print_summary(res: dict) -> None:
    iv, pb, lt = res["interval_s"], res["payload_bytes"], res["latency_ms"]
    print("===== ИТОГ =====")
    print(f"Длительность, с:   {res['duration_s']:g}")
    print(f"Сообщений:         {res['messages']} (устройств: {res['devices']}, QoS {res['qos']})")
    if iv:
        print(f"Интервал, с:       ср={iv['mean']:.4f}  σ={iv['stdev']:.4f}  "
              f"min={iv['min']:.3f}  max={iv['max']:.3f}")
    if pb:
        print(f"Payload, Б:        ср={pb['mean']:.1f}  min={pb['min']}  max={pb['max']}")
        print(f"MQTT PUBLISH, Б:   ср={res['mqtt_publish_bytes_mean']:.1f}")
    if lt:
        print(f"Задержка, мс:      p50={lt['p50']:.1f}  p95={lt['p95']:.1f}  max={lt['max']:.1f}")
    print(f"Пропуски seq:      {res['seq_gaps']}   дубли: {res['seq_duplicates']}   "
          f"подключений: {res['connects']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--topic", default="enu/+/+/up/telemetry")
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--out")
    ap.add_argument("--persistent", action="store_true",
                    help="persistent-сессия (clean_session=False): брокер копит сообщения, "
                         "пока наблюдатель отключён")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")   # консоль Windows по умолчанию cp1251

    arrivals: dict[str, list[float]] = defaultdict(list)
    sizes: list[int] = []
    wire: list[int] = []
    latency_ms: list[float] = []
    seqs: dict[str, list[int]] = defaultdict(list)
    qos_seen: set[int] = set()

    def on_message(_c, _u, m: mqtt.MQTTMessage) -> None:
        now = time.time()
        arrivals[m.topic].append(now)
        sizes.append(len(m.payload))
        wire.append(mqtt_publish_size(m.topic, len(m.payload), m.qos))
        qos_seen.add(m.qos)
        try:
            msg = json.loads(m.payload)
            latency_ms.append((now - msg["ts"]) * 1000)
            seqs[msg["device_id"]].append(msg["seq"])
        except (ValueError, KeyError):
            pass

    reconnects = []
    if args.persistent:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="measure-persistent",
                        clean_session=False)
    else:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"measure-{int(time.time())}")
    c.reconnect_delay_set(min_delay=1, max_delay=5)

    def on_connect(cl, _u, _f, _rc, _p) -> None:
        reconnects.append(time.time())
        cl.subscribe(args.topic, qos=1)

    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(args.host, args.port, keepalive=30)
    c.loop_start()
    time.sleep(args.duration)
    c.loop_stop()
    c.disconnect()

    intervals = [b - a for ts in arrivals.values() for a, b in zip(ts, ts[1:])]
    gaps = dups = 0
    for s in seqs.values():
        for a, b in zip(s, s[1:]):
            if b == a:
                dups += 1
            elif b > a + 1:
                gaps += b - a - 1

    res = {
        "duration_s": args.duration,
        "messages": len(sizes),
        "devices": len(seqs),
        "qos": sorted(qos_seen),
        "interval_s": {
            "mean": statistics.mean(intervals), "stdev": statistics.pstdev(intervals),
            "min": min(intervals), "p50": pct(intervals, 50),
            "p95": pct(intervals, 95), "max": max(intervals),
        } if intervals else None,
        "payload_bytes": {"min": min(sizes), "mean": statistics.mean(sizes), "max": max(sizes)}
        if sizes else None,
        "mqtt_publish_bytes_mean": statistics.mean(wire) if wire else None,
        "latency_ms": {
            "p50": pct(latency_ms, 50), "p95": pct(latency_ms, 95), "max": max(latency_ms),
        } if latency_ms else None,
        "seq_gaps": gaps,
        "seq_duplicates": dups,
        "connects": len(reconnects),
    }
    print_summary(res)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
