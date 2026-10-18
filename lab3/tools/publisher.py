"""Publisher × N: каждое устройство — отдельный MQTT-клиент с LWT, 4 метрики с seq.

    python tools/publisher.py --devices 5 --interval 2 --duration 60
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).parent))
from topics import METRICS, status_topic, topic  # noqa: E402

V2 = mqtt.CallbackAPIVersion.VERSION2
BASE = {"temperature": 21.0, "humidity": 45.0, "pressure": 972.0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="lab3-mosquitto")
    ap.add_argument("--devices", type=int, default=5)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--qos", type=int, default=1)
    ap.add_argument("--drop-every", type=int, default=0,
                    help="пропускать каждое N-е сообщение temperature (проверка детекции пропусков)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    devs = [f"dev-{i:03d}" for i in range(1, args.devices + 1)]
    clients, seq, value = {}, {}, {}
    for d in devs:
        c = mqtt.Client(V2, client_id=d)
        c.will_set(status_topic(d), "offline", qos=1, retain=True)
        c.connect(args.host, 1883, keepalive=30)
        c.loop_start()
        c.publish(status_topic(d), "online", qos=1, retain=True)
        clients[d] = c
        for m in METRICS:
            seq[(d, m)] = 0
            value[(d, m)] = BASE.get(m, 0.0)
    sent = skipped = 0
    t0 = time.time()
    k = 0
    while time.time() - t0 < args.duration:
        for d in devs:
            for m, (unit, _) in METRICS.items():
                seq[(d, m)] += 1
                if m == "motion":
                    v = random.random() < 0.1
                else:
                    value[(d, m)] += random.gauss(0, 0.05)
                    v = round(value[(d, m)], 2)
                if args.drop_every and m == "temperature" and seq[(d, m)] % args.drop_every == 0:
                    skipped += 1                       # имитация потери: номер израсходован, сообщения нет
                    continue
                payload = json.dumps({"seq": seq[(d, m)], "ts": int(time.time() * 1000), "value": v,
                                      "unit": unit}, separators=(",", ":"))
                clients[d].publish(topic(d, m), payload, qos=args.qos)
                sent += 1
        k += 1
        time.sleep(max(0.0, t0 + k * args.interval - time.time()))
    for d, c in clients.items():
        c.publish(status_topic(d), "online", qos=1, retain=True)
        c.loop_stop()
        c.disconnect()                                  # штатное отключение: LWT не публикуется
    print(f"publisher: {len(devs)} устройств × {len(METRICS)} метрики, отправлено {sent}, "
          f"намеренно пропущено {skipped}, QoS {args.qos}")


if __name__ == "__main__":
    main()
