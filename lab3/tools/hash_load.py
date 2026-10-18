"""Что видит подписка # при большом парке: генератор (lab3-device) и подписчик (lab3-backend).

    python tools/hash_load.py gen --devices 2000 --interval 2 --duration 30          # в lab3-device
    python tools/hash_load.py sub --filter 'enu/#' --duration 40                      # в lab3-backend
"""

import argparse
import json
import resource
import statistics as st
import sys
import time

import paho.mqtt.client as mqtt

V2 = mqtt.CallbackAPIVersion.VERSION2
METRICS = ("temperature", "humidity", "pressure", "motion")


def gen(a) -> None:
    c = mqtt.Client(V2, client_id="loadgen")
    c.max_queued_messages_set(0)
    c.connect("lab3-mosquitto", 1883)
    c.loop_start()
    t0, k, sent = time.time(), 0, 0
    while time.time() - t0 < a.duration:
        for d in range(a.devices):
            for m in METRICS:
                pl = json.dumps({"seq": k, "ts": time.time() * 1000, "value": 21.5}, separators=(",", ":"))
                c.publish(f"enu/site1/dev-{d:05d}/up/telemetry/{m}", pl, qos=0)
                sent += 1
        k += 1
        time.sleep(max(0.0, t0 + k * a.interval - time.time()))
    time.sleep(2)
    c.loop_stop()
    print(f"генератор: {a.devices} устройств × 4 метрики раз в {a.interval:g} с = "
          f"{a.devices * 4 / a.interval:.0f} сообщений/с, отправлено {sent} за {time.time() - t0:.0f} с")


def sub(a) -> None:
    lag, per_sec, n, other = [], {}, [0], [0]
    topics = set()

    def on_msg(_c, _u, m):
        now = time.time()
        n[0] += 1
        topics.add(m.topic)
        per_sec[int(now)] = per_sec.get(int(now), 0) + 1
        try:
            lag.append(now * 1000 - json.loads(m.payload)["ts"])
        except (ValueError, KeyError):
            other[0] += 1        # # приносит и чужие форматы: retained status, config и т.п.

    c = mqtt.Client(V2, client_id=f"hash-{time.time_ns()}")
    c.on_message = on_msg
    c.on_connect = lambda cl, *_: cl.subscribe(a.filter, qos=0)
    c.connect("lab3-mosquitto", 1883)
    cpu0 = resource.getrusage(resource.RUSAGE_SELF)
    c.loop_start()
    time.sleep(a.duration)
    c.loop_stop()
    cpu1 = resource.getrusage(resource.RUSAGE_SELF)
    cpu = (cpu1.ru_utime - cpu0.ru_utime) + (cpu1.ru_stime - cpu0.ru_stime)
    busy = sorted(per_sec.values())[-10:] if per_sec else [0]
    s = sorted(lag) if lag else [float("nan")]
    print(f"подписка {a.filter}: сообщений {n[0]} (не телеметрия: {other[0]}), топиков {len(topics)}, "
          f"пик {max(busy)} сообщ./с, "
          f"CPU подписчика {cpu:.1f} с за {a.duration:.0f} с ({100 * cpu / a.duration:.0f} % ядра)")
    print(f"  задержка (отставание) p50 {s[len(s) // 2]:.0f} мс, p99 {s[int(0.99 * (len(s) - 1))]:.0f} мс, "
          f"макс {s[-1]:.0f} мс; среднее {st.mean(lag) if lag else float('nan'):.0f} мс")


def main() -> None:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("gen")
    g.add_argument("--devices", type=int, default=2000)
    g.add_argument("--interval", type=float, default=2)
    g.add_argument("--duration", type=float, default=30)
    s = sp.add_parser("sub")
    s.add_argument("--filter", default="enu/#")
    s.add_argument("--duration", type=float, default=40)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    gen(a) if a.cmd == "gen" else sub(a)


if __name__ == "__main__":
    main()
