"""Приложение на стороне стенда: подписка на uplink-события и оценка PDR по разрывам fCnt.

Приложение не знает, сколько кадров отправлено, — только что пришло. Пропуски номеров fCnt
между первым и последним принятым кадром каждого устройства дают оценку потерь в эфире.

    python tools/app_counter.py --idle 5
"""

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict

import paho.mqtt.client as mqtt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.getenv("MQTT_HOST", "localhost"))
    ap.add_argument("--idle", type=float, default=5, help="завершить после N секунд тишины")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    fc: dict[str, list[int]] = defaultdict(list)
    sf = Counter()
    rssi: list[int] = []
    last = [time.time()]
    first_printed = [0]

    def on_msg(_c, _u, m):
        ev = json.loads(m.payload)
        fc[ev["deviceInfo"]["devEui"]].append(ev["fCnt"])
        sf[ev["txInfo"]["modulation"]["lora"]["spreadingFactor"]] += 1
        rssi.append(ev["rxInfo"][0]["rssi"])
        last[0] = time.time()
        if first_printed[0] < 3:
            first_printed[0] += 1
            print(f"{m.topic} {json.dumps(ev, ensure_ascii=False)[:230]}…")

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="lab-s1-app")
    c.on_message = on_msg
    c.on_connect = lambda cl, *_: cl.subscribe("application/+/device/+/event/up", qos=1)
    c.connect(args.host, 1883)
    c.loop_start()
    while time.time() - last[0] < args.idle or not fc:
        time.sleep(0.5)
    c.loop_stop()

    rx = sum(len(v) for v in fc.values())
    expected = sum(max(v) - min(v) + 1 for v in fc.values())
    print(f"приложение: устройств {len(fc)}, принято кадров {rx}, ожидалось по fCnt {expected}, "
          f"пропусков {expected - rx} → PDR(fCnt) {100 * rx / expected:.1f} %")
    print("  по SF: " + ", ".join(f"SF{k} {v}" for k, v in sorted(sf.items()))
          + f"; RSSI от {min(rssi)} до {max(rssi)} дБм")
    print("  примечание: кадры, потерянные до первого или после последнего принятого, по fCnt не видны")


if __name__ == "__main__":
    main()
