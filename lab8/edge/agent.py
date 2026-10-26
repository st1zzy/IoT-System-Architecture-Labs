"""Edge-агент edge-01: из 200 сообщений/с в облако уходят только результаты.

EDGE_MODE=edge: окно 60 с → min/max/avg/std/p95 по каждой метрике каждого устройства (одно сообщение
на устройство в минуту); детектор аномалий → аларм немедленно + «сырое» окно ±30 с; локальное правило
«температура > 80 °C → команда cooling_on» на устройство без участия облака; store-and-forward в SQLite
с удалением после PUBACK.
EDGE_MODE=raw: базовая линия для сравнения — каждое сырое сообщение пересылается в облако (QoS 0),
правила и детектор работают в облаке; команды облака агент ретранслирует на устройство.
HTTP :8091 /stats.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import paho.mqtt.client as mqtt

from detect import Detector
from model import METRICS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("edge")
MODE = os.getenv("EDGE_MODE", "edge")
EDGE = "edge-01"
CLOUD = os.getenv("CLOUD_HOST", "lab8-cloud")
LOCAL = os.getenv("LOCAL_HOST", "lab8-local")
WINDOW_S, RAW_HALF_S = 60, 30
V2 = mqtt.CallbackAPIVersion.VERSION2
LOCK = threading.Lock()
STATS = {k: 0 for k in ("in_msgs", "in_bytes", "out_msgs", "out_bytes", "sent_msgs", "sent_bytes", "agg_msgs",
                        "alarms", "raw_windows", "rule_cmds", "relayed_cmds", "fwd_msgs", "fwd_dropped",
                        "connects", "disconnects")}
STATS.update(connected=False, mode=MODE)


class Buffer:
    """Очередь на отправку: удаление только после PUBACK (как в шлюзе Lab 7)."""

    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS buf(seq INTEGER PRIMARY KEY AUTOINCREMENT, prio INTEGER, "
                        "topic TEXT, payload TEXT)")
        self.lock = threading.Lock()

    def put(self, topic: str, obj: dict, prio: int) -> None:
        with self.lock:
            self.db.execute("INSERT INTO buf(prio, topic, payload) VALUES (?, ?, ?)",
                            (prio, topic, json.dumps(obj, separators=(",", ":"))))
        WAKE.set()

    def take(self, limit: int, skip: set[int]) -> list[tuple[int, str, str]]:
        with self.lock:
            rows = self.db.execute("SELECT seq, topic, payload FROM buf ORDER BY prio, seq LIMIT ?",
                                   (limit + len(skip),)).fetchall()
        return [r for r in rows if r[0] not in skip][:limit]

    def delete(self, seq: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM buf WHERE seq=?", (seq,))

    def size(self) -> int:
        with self.lock:
            return self.db.execute("SELECT count(*) FROM buf").fetchone()[0]


BUF = Buffer("/data/uplink.db")
WAKE = threading.Event()
CLOUD_CLIENT: list[mqtt.Client | None] = [None]
DET: dict[tuple[str, str], Detector] = defaultdict(Detector)
WIN: dict[tuple[str, int], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
RAW: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=120))
PENDING_RAW: list[tuple[float, str, str, int]] = []
RULE_ACTIVE: dict[str, bool] = {}
local = mqtt.Client(V2, client_id=f"{EDGE}-local")


def on_local(_c, _u, m) -> None:
    with LOCK:
        STATS["in_msgs"] += 1
        STATS["in_bytes"] += len(m.topic) + len(m.payload)
    if MODE == "raw":
        cl = CLOUD_CLIENT[0]
        if cl is not None and STATS["connected"]:
            cl.publish(f"edge/{EDGE}/raw/{m.topic.split('/')[1]}", m.payload, qos=0)
            with LOCK:
                STATS["fwd_msgs"] += 1
                STATS["sent_bytes"] += len(m.topic) + len(m.payload)
        else:
            STATS["fwd_dropped"] += 1
        return
    d = json.loads(m.payload)
    dev, ts = d["dev"], d["ts"]
    w0 = ts // 1000 // WINDOW_S * WINDOW_S
    # локальное правило: реакция без облака
    t = d["temperature"]
    if t > 80 and not RULE_ACTIVE.get(dev):
        RULE_ACTIVE[dev] = True
        local.publish(f"site/{dev}/cmd", json.dumps({"cmd": "cooling_on", "ts": ts, "by": "edge"}), qos=1)
        STATS["rule_cmds"] += 1
    elif t < 75:
        RULE_ACTIVE[dev] = False
    with LOCK:
        win = WIN[(dev, w0)]
        for metric in METRICS:
            v = d[metric]
            win[metric].append(v)
            RAW[(dev, metric)].append((ts, v))
            ev = DET[(dev, metric)].update(v, ts / 1000)
            if ev:
                STATS["alarms"] += 1
                BUF.put(f"edge/{EDGE}/alarm/{dev}", {"dev": dev, "metric": metric, "type": ev[0],
                                                       "score": round(ev[1], 1), "ts": ts, "v": v}, prio=0)
                PENDING_RAW.append((time.time() + RAW_HALF_S + 1, dev, metric, ts))


def stats5(vals: list[float], dec: int) -> list[float]:
    a = np.asarray(vals)
    return [round(float(x), dec + 1) for x in (a.min(), a.max(), a.mean(), a.std(), np.percentile(a, 95))]


def flusher() -> None:
    """Раз в секунду: закрыть завершённые окна (агрегаты) и отправить созревшие сырые окна ±30 с."""
    while True:
        time.sleep(1)
        now = time.time()
        with LOCK:
            done = [k for k in WIN if k[1] + WINDOW_S + 2 <= now]   # окно закрыто, 2 с на опоздавшие
            closed = {k: WIN.pop(k) for k in done}
            due = [p for p in PENDING_RAW if p[0] <= now]
            for p in due:
                PENDING_RAW.remove(p)
            raws = []
            for _, dev, metric, ts in due:
                pts = [(t, v) for t, v in RAW[(dev, metric)] if abs(t - ts) <= RAW_HALF_S * 1000]
                raws.append((dev, {"dev": dev, "metric": metric, "alarm_ts": ts, "t": [p[0] for p in pts],
                                   "v": [p[1] for p in pts]}))
        for (dev, w0), win in closed.items():
            BUF.put(f"edge/{EDGE}/agg/{dev}", {"dev": dev, "w0": w0, "n": len(win["temperature"]),
                                                "m": {mt: stats5(v, METRICS[mt]["dec"]) for mt, v in win.items()}},
                    prio=1)
            STATS["agg_msgs"] += 1
        for dev, obj in raws:
            BUF.put(f"edge/{EDGE}/rawwin/{dev}", obj, prio=1)
            STATS["raw_windows"] += 1


def uplink() -> None:
    """Подключение к облаку: новый клиент на каждое подключение, буфер — единственный источник истины."""
    while True:
        inflight: dict[int, tuple[int, int]] = {}
        ilock = threading.Lock()
        up, down = threading.Event(), threading.Event()

        def on_connect(cl, _u, _f, rc, _p):
            if not rc.is_failure:
                cl.subscribe(f"edge/{EDGE}/cmd/+", qos=1)
                up.set()

        def on_publish(_c, _u, mid, _rc, _p):
            with ilock:
                item = inflight.pop(mid, None)
            if item:
                BUF.delete(item[0])
                with LOCK:
                    STATS["out_msgs"] += 1
                    STATS["out_bytes"] += item[1]
            WAKE.set()

        def on_cmd(_c, _u, m):            # команда облака → устройство (режим raw)
            dev = m.topic.rsplit("/", 1)[1]
            local.publish(f"site/{dev}/cmd", m.payload, qos=1)
            STATS["relayed_cmds"] += 1

        def on_disconnect(*_):
            down.set()
            WAKE.set()

        c = mqtt.Client(V2, client_id=EDGE, clean_session=True)
        c.max_inflight_messages_set(20)
        c.on_connect, c.on_publish, c.on_message, c.on_disconnect = on_connect, on_publish, on_cmd, on_disconnect
        try:
            c.connect(CLOUD, 1883, keepalive=10)
        except OSError:
            time.sleep(2)
            continue
        c.loop_start()
        if not up.wait(5):
            c.loop_stop()
            c.disconnect()
            continue
        CLOUD_CLIENT[0] = c
        STATS["connected"] = True
        STATS["connects"] += 1
        log.info("cloud: подключено, в буфере %d", BUF.size())
        while not down.is_set():
            with ilock:
                busy = {v[0] for v in inflight.values()}
                room = 20 - len(inflight)
            rows = BUF.take(room, busy) if room > 0 else []
            for seq, topic, payload in rows:
                info = c.publish(topic, payload, qos=1)
                with ilock:
                    inflight[info.mid] = (seq, len(topic) + len(payload))
                with LOCK:
                    STATS["sent_msgs"] += 1
                    STATS["sent_bytes"] += len(topic) + len(payload)
            if not rows:
                WAKE.wait(0.1)
                WAKE.clear()
        CLOUD_CLIENT[0] = None
        STATS["connected"] = False
        STATS["disconnects"] += 1
        log.warning("cloud: соединение потеряно, в буфере %d", BUF.size())
        c.loop_stop()
        try:
            c.disconnect()
        except Exception:  # noqa: BLE001
            pass


class Api(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        with LOCK:
            body = {**STATS, "buffer": BUF.size(), "t": time.time()}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    local.on_message = on_local
    local.on_connect = lambda cl, *_: cl.subscribe("site/+/telemetry", qos=0)
    local.connect(LOCAL, 1883)
    local.loop_start()
    for fn in (flusher, uplink):
        threading.Thread(target=fn, daemon=True).start()
    log.info("edge-01: режим %s", MODE)
    ThreadingHTTPServer(("0.0.0.0", 8091), Api).serve_forever()
