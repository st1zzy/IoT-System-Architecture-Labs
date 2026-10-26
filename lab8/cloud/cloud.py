"""Облачная сторона Lab 8: приём результатов edge → TimescaleDB, учёт трафика и алармов.

В режиме raw (edge пересылает сырые сообщения) здесь же работают тот же детектор аномалий и то же
правило «температура > 80 °C», а команда уходит обратно через облачный брокер: edge/edge-01/cmd/<dev>.
HTTP :8092: GET /dump — всё полученное за прогон, POST /reset.
"""

import json
import logging
import os
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt
import psycopg

from detect import Detector
from model import METRICS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cloud")
LOCK = threading.Lock()
S: dict = {}
DET: dict = {}
RULE: dict[str, bool] = {}


def reset() -> None:
    with LOCK:
        S.clear()
        S.update(t0=time.time(), msgs=defaultdict(int), bytes=defaultdict(int), alarms=[], cloud_alarms=[],
                 agg=[], rawwin=[], cmds=0)
        DET.clear()
        RULE.clear()


def db() -> psycopg.Connection:
    for _ in range(60):
        try:
            conn = psycopg.connect(os.getenv("PG_DSN", "postgresql://postgres:iot@lab8-db:5432/iot"), autocommit=True)
            break
        except psycopg.OperationalError:
            time.sleep(1)
    conn.execute("CREATE TABLE IF NOT EXISTS edge_agg(w0 timestamptz NOT NULL, dev text NOT NULL, metric text NOT NULL,"
                 " min double precision, max double precision, avg double precision, std double precision,"
                 " p95 double precision, PRIMARY KEY (dev, metric, w0))")
    conn.execute("SELECT create_hypertable('edge_agg', 'w0', if_not_exists => true)")
    conn.execute("CREATE TABLE IF NOT EXISTS edge_alarm(ts timestamptz NOT NULL, dev text, metric text, type text,"
                 " score double precision, its timestamptz DEFAULT now())")
    return conn


DB = db()
cloud = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cloud-svc", clean_session=False)


def on_message(_c, _u, m) -> None:
    its = time.time()
    kind = m.topic.split("/")[2]
    d = json.loads(m.payload)
    with LOCK:
        S["msgs"][kind] += 1
        S["bytes"][kind] += len(m.topic) + len(m.payload)
    if kind == "agg":
        with LOCK:
            S["agg"].append(d)
        DB.cursor().executemany(
            "INSERT INTO edge_agg VALUES (to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [(d["w0"], d["dev"], mt, *v) for mt, v in d["m"].items()])
    elif kind == "alarm":
        with LOCK:
            S["alarms"].append({**d, "its": its})
        DB.execute("INSERT INTO edge_alarm(ts, dev, metric, type, score) VALUES (to_timestamp(%s / 1000.0), %s, %s, %s, %s)",
                   (d["ts"], d["dev"], d["metric"], d["type"], d["score"]))
    elif kind == "rawwin":
        with LOCK:
            S["rawwin"].append({"dev": d["dev"], "metric": d["metric"], "alarm_ts": d["alarm_ts"], "n": len(d["v"])})
    elif kind == "raw":                       # базовая линия: правило и детектор в облаке
        dev, ts = d["dev"], d["ts"]
        if d["temperature"] > 80 and not RULE.get(dev):
            RULE[dev] = True
            cloud.publish(f"edge/edge-01/cmd/{dev}", json.dumps({"cmd": "cooling_on", "ts": ts, "by": "cloud"}), qos=1)
            with LOCK:
                S["cmds"] += 1
        elif d["temperature"] < 75:
            RULE[dev] = False
        for mt in METRICS:
            ev = DET.setdefault((dev, mt), Detector()).update(d[mt], ts / 1000)
            if ev:
                with LOCK:
                    S["cloud_alarms"].append({"dev": dev, "metric": mt, "type": ev[0], "ts": ts, "its": its})


class Api(BaseHTTPRequestHandler):
    def _send(self, obj) -> None:
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        with LOCK:
            self._send(S)

    def do_POST(self):  # noqa: N802
        reset()
        self._send({"reset": True})

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    reset()
    cloud.on_message = on_message
    cloud.on_connect = lambda cl, *_: cl.subscribe([("edge/+/agg/+", 1), ("edge/+/alarm/+", 1),
                                                    ("edge/+/rawwin/+", 1), ("edge/+/raw/+", 0)])
    cloud.connect(os.getenv("CLOUD_HOST", "lab8-cloud"), 1883, keepalive=30)
    cloud.loop_start()
    log.info("cloud-svc: подписка edge/+/(agg|alarm|rawwin|raw)/+")
    ThreadingHTTPServer(("0.0.0.0", 8092), Api).serve_forever()
