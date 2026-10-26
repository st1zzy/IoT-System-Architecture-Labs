"""Симулятор объекта: N устройств × 5 метрик, 1 сообщение/с на устройство, аномалии по расписанию.

HTTP :8090: GET /truth — истинные эпизоды; POST /overheat?dev=N — перегрев устройства N (температура 85 °C
на 5 с); GET /latency — задержки реакции: от публикации первого отсчёта > 80 °C до прихода команды на
site/dev-NNN/cmd; POST /reset — сбросить замеры задержки.
"""

import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import paho.mqtt.client as mqtt

from model import Device, schedule

N = int(os.getenv("DEVICES", "200"))
SEED = int(os.getenv("SEED", "11"))
DURATION = int(os.getenv("DURATION", "600"))
ANOMALIES = int(os.getenv("ANOMALIES", "60"))
LOCK = threading.Lock()
OVERHEAT: dict[int, int] = {}          # устройство → оставшиеся секунды перегрева
TRIALS: dict[int, dict] = {}           # устройство → открытая попытка {t_pub}
LATENCY: list[dict] = []
OVERHEAT_LOG: list[dict] = []         # перегревы — управляющие события, не аномалии датчиков
T0 = time.time()

rng = random.Random(SEED + 1000)
devs = [Device(i, rng) for i in range(N)]
truth = schedule(SEED, N, DURATION, ANOMALIES)
starts: dict[int, list] = {}
for a in truth:
    starts.setdefault(a.start, []).append(a)


def on_cmd(_c, _u, m) -> None:
    t = time.time()
    dev = int(m.topic.split("/")[1].split("-")[1])
    with LOCK:
        tr = TRIALS.pop(dev, None)
    if tr:
        LATENCY.append({"dev": dev, "t_pub": tr["t_pub"], "latency_ms": (t - tr["t_pub"]) * 1000,
                        "cmd": json.loads(m.payload).get("cmd")})


c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="site-sim")
c.max_queued_messages_set(0)
c.on_message = on_cmd
c.on_connect = lambda cl, *_: cl.subscribe("site/+/cmd", qos=1)
c.connect(os.getenv("MQTT_HOST", "lab8-local"), 1883)
c.loop_start()


def loop() -> None:
    k = 0
    while True:
        for a in starts.get(k, []):
            devs[a.dev].anom[a.metric] = a
        ts = int(time.time() * 1000)
        for i, d in enumerate(devs):
            m = d.sample(k)
            with LOCK:
                if OVERHEAT.get(i, 0) > 0:
                    m["temperature"] = 85.0
                    OVERHEAT[i] -= 1
            msg = {"dev": f"dev-{i:03d}", "seq": k, "ts": ts, **m}
            if m["temperature"] > 80:
                with LOCK:
                    if i in TRIALS and "t_pub" not in TRIALS[i]:
                        TRIALS[i]["t_pub"] = time.time()
            c.publish(f"site/dev-{i:03d}/telemetry", json.dumps(msg, separators=(",", ":")), qos=0)
        k += 1
        time.sleep(max(0.0, T0 + k - time.time()))


class Api(BaseHTTPRequestHandler):
    def _send(self, obj) -> None:
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/truth"):
            self._send({"t0": T0, "seed": SEED, "episodes": [a.as_dict() for a in truth], "overheat": OVERHEAT_LOG})
        else:
            with LOCK:
                self._send({"latency": list(LATENCY), "open": len(TRIALS)})

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/overheat":
            dev = int(parse_qs(u.query)["dev"][0])
            with LOCK:
                OVERHEAT[dev] = 5
                TRIALS[dev] = {}
                OVERHEAT_LOG.append({"dev": f"dev-{dev:03d}", "t": time.time() - T0})
        elif u.path == "/reset":
            with LOCK:
                LATENCY.clear()
                TRIALS.clear()
        self._send({"ok": True})

    def log_message(self, *_):
        pass


threading.Thread(target=loop, daemon=True).start()
print(f"sim: {N} устройств, seed {SEED}, {len(truth)} аномалий за {DURATION} с", flush=True)
ThreadingHTTPServer(("0.0.0.0", 8090), Api).serve_forever()
