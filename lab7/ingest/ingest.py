"""Северная сторона: приём от шлюза → TimescaleDB (идемпотентно по (gw, seq, dts)) + учёт для экспериментов.

Для каждой записи фиксируется время прихода (ingest_ts): по нему считаются нагрузка на брокер
в секунду, задержка доставки и порядок прихода алармов. HTTP :8082 — /stats, /dump, POST /reset.
"""

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt
import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest")
DSN = os.getenv("PG_DSN", "postgresql://postgres:iot@lab7-db:5432/iot")
LOCK = threading.Lock()
STATE: dict = {}


def reset() -> None:
    with LOCK:
        STATE.clear()
        STATE.update(t0=time.time(), msgs=0, records=0, db_inserted=0, db_dup=0, arrivals=[], dropped_total=0)


def db_connect() -> psycopg.Connection:
    for _ in range(60):
        try:
            conn = psycopg.connect(DSN, autocommit=True)
            break
        except psycopg.OperationalError:
            time.sleep(1)
    conn.execute("CREATE TABLE IF NOT EXISTS telemetry(gw text NOT NULL, seq bigint NOT NULL, dts timestamptz NOT NULL,"
                 " gts timestamptz, its timestamptz NOT NULL DEFAULT now(), asset text, tag text,"
                 " value double precision, unit text, quality text[], PRIMARY KEY (gw, seq, dts))")
    conn.execute("SELECT create_hypertable('telemetry', 'dts', if_not_exists => true)")
    conn.execute("CREATE TABLE IF NOT EXISTS alarms(gw text NOT NULL, seq bigint NOT NULL, dts timestamptz NOT NULL,"
                 " its timestamptz NOT NULL DEFAULT now(), asset text, rule text, state text, severity text,"
                 " value double precision, PRIMARY KEY (gw, seq))")
    return conn


DB = db_connect()


def on_message(_c, _u, m) -> None:
    its = time.time()
    body = json.loads(m.payload)
    alarm = m.topic.endswith("/alarm")
    recs = [body] if alarm else body["records"]
    with LOCK:
        STATE["msgs"] += 1
        STATE["records"] += len(recs)
        if not alarm:
            STATE["dropped_total"] = max(STATE["dropped_total"], body.get("dropped_total", 0))
        for r in recs:
            STATE["arrivals"].append((r["seq"], 0 if alarm else 1, r["dts"], r["gts"], round(its * 1000)))
    with DB.cursor() as cur:
        if alarm:
            cur.execute("INSERT INTO alarms(gw, seq, dts, asset, rule, state, severity, value) VALUES "
                        "(%s, %s, to_timestamp(%s / 1000.0), %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (body["gw"], body["seq"], body["dts"], body["asset"], body["rule"], body["state"],
                         body["severity"], body["v"]))
            ins = cur.rowcount
        else:
            cur.executemany("INSERT INTO telemetry(gw, seq, dts, gts, asset, tag, value, unit, quality) VALUES "
                            "(%s, %s, to_timestamp(%s / 1000.0), to_timestamp(%s / 1000.0), %s, %s, %s, %s, %s) "
                            "ON CONFLICT DO NOTHING",
                            [(r["gw"], r["seq"], r["dts"], r["gts"], r["asset"], r["tag"], r["v"], r["u"],
                              r.get("q")) for r in recs], returning=False)
            ins = cur.rowcount
    with LOCK:
        STATE["db_inserted"] += max(ins, 0)
        STATE["db_dup"] += len(recs) - max(ins, 0)


def summary() -> dict:
    with LOCK:
        arr = list(STATE["arrivals"])
        out = {k: v for k, v in STATE.items() if k != "arrivals"}
    seqs = [a[0] for a in arr]
    uniq = set(seqs)
    out["unique"] = len(uniq)
    out["dups"] = len(seqs) - len(uniq)
    if uniq:
        lo, hi = min(uniq), max(uniq)
        out.update(seq_min=lo, seq_max=hi, missing=hi - lo + 1 - len(uniq))
    per_sec: dict[int, int] = {}
    for a in arr:
        per_sec[a[4] // 1000] = per_sec.get(a[4] // 1000, 0) + 1
    out["peak_records_per_s"] = max(per_sec.values()) if per_sec else 0
    return out


class Api(BaseHTTPRequestHandler):
    def _send(self, obj) -> None:
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/dump"):
            with LOCK:
                self._send({"arrivals": STATE["arrivals"], "t0": STATE["t0"]})
        else:
            self._send(summary())

    def do_POST(self):  # noqa: N802
        reset()
        self._send({"reset": True})

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    reset()
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ingest", clean_session=False)
    c.on_message = on_message
    c.on_connect = lambda cl, *_: cl.subscribe("enu/+/+/up/#", qos=1)
    c.connect(os.getenv("NORTH_HOST", "lab7-north"), 1883, keepalive=30)
    c.loop_start()
    log.info("ingest: подписка enu/+/+/up/#, БД %s", DSN.split("@")[-1])
    ThreadingHTTPServer(("0.0.0.0", 8082), Api).serve_forever()
