"""Генератор нагрузки: N виртуальных устройств, каждое — сообщение Lab 1 раз в секунду, QoS 1.

Устройства разделены между W процессами (по одному MQTT-соединению на процесс).
    python tools/loadgen.py --devices 2000 --duration 30 --workers 4
"""

import argparse
import json
import multiprocessing as mp
import random
import time

import paho.mqtt.client as mqtt


def run(wid: int, devs: list[int], duration: float, interval: float, host: str, out) -> None:
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"loadgen-{wid}-{time.time_ns()}")
    c.max_inflight_messages_set(1000)
    c.max_queued_messages_set(0)
    c.connect(host, 1883, keepalive=60)
    c.loop_start()
    seq = {d: 0 for d in devs}
    t0 = time.time() + random.uniform(0, 0.1)
    k = sent = 0
    while time.time() - t0 < duration:
        for d in devs:
            seq[d] += 1
            msg = {"schema_version": "1.0", "device_id": f"dev-{d:05d}", "ts": int(time.time() * 1000),
                   "seq": seq[d], "metrics": {"temperature": round(21 + random.gauss(0, 1), 2),
                                              "humidity": round(45 + random.gauss(0, 3), 1),
                                              "pressure": round(972 + random.gauss(0, 1), 2),
                                              "motion": random.random() < 0.05},
                   "battery": 87.0, "rssi": -67}
            c.publish(f"enu/site{d % 4 + 1}/dev-{d:05d}/up/telemetry", json.dumps(msg, separators=(",", ":")), qos=1)
            sent += 1
        k += 1
        time.sleep(max(0.0, t0 + k * interval - time.time()))
    time.sleep(2)
    c.loop_stop()
    c.disconnect()
    out.put(sent)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", type=int, required=True)
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--host", default="lab5-mosquitto")
    a = ap.parse_args()
    out = mp.Queue()
    ps = [mp.Process(target=run, args=(w, list(range(w, a.devices, a.workers)), a.duration, a.interval, a.host, out))
          for w in range(a.workers)]
    for p in ps:
        p.start()
    for p in ps:
        p.join()
    sent = sum(out.get() for _ in ps)
    print(f"loadgen: {a.devices} устройств × 1/{a.interval:g} с = {a.devices / a.interval:.0f} сообщ./с, "
          f"отправлено {sent} за {a.duration:g} с")


if __name__ == "__main__":
    main()
