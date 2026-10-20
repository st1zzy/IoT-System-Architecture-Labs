"""IoT-шлюз gw-01: drivers → normalize → rules / deadband → persistent buffer (SQLite WAL) → publisher.

Южная сторона: Modbus TCP (опрос раз в секунду) и legacy-MQTT устройства на локальном брокере.
Северная сторона: MQTT QoS 1, пакеты по BATCH записей, запись удаляется из буфера только после PUBACK.
Буфер — единственный источник истины: при каждом подключении создаётся новый MQTT-клиент, всё
неподтверждённое отправляется заново из буфера. Алармы — приоритет 0: публикуются немедленно,
минуя батчинг и ограничитель скорости, и после обрыва уходят первыми.

Переменные окружения: BUFFER_MAX (10000), BATCH (20), BACKFILL_RATE записей/с (200; 0 — без ограничения),
DB_MODE (abs | pct | off), DB_SCALE (1.0), ACK_MODE (puback | send), RAW_LOG (0/1), KEEPALIVE (10).
"""

import json
import logging
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt
import yaml
from pymodbus.client import ModbusTcpClient

from filters import Deadband

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gw")
CFG = yaml.safe_load(open("/app/mapping.yaml", encoding="utf-8"))
GW, SITE = CFG["gateway"], CFG["site"]
NORTH = os.getenv("NORTH_HOST", "lab7-north")
BUFFER_MAX = int(os.getenv("BUFFER_MAX", "10000"))
BATCH = int(os.getenv("BATCH", "20"))
MAX_DELAY = float(os.getenv("MAX_DELAY", "1.0"))
RATE = float(os.getenv("BACKFILL_RATE", "200"))
DB_MODE = os.getenv("DB_MODE", "abs")
DB_SCALE = float(os.getenv("DB_SCALE", "1.0"))
ACK_MODE = os.getenv("ACK_MODE", "puback")
RAW_LOG = os.getenv("RAW_LOG", "0") == "1"
KEEPALIVE = int(os.getenv("KEEPALIVE", "10"))
MAX_INFLIGHT = 20
V2 = mqtt.CallbackAPIVersion.VERSION2
T_BATCH = f"enu/{SITE}/{GW}/up/batch"
T_ALARM = f"enu/{SITE}/{GW}/up/alarm"
T_STATUS = f"enu/{SITE}/{GW}/status"

STATS = {k: 0 for k in ("samples", "filtered", "enqueued", "alarms", "sent_records", "acked_records",
                        "sent_msgs", "dropped", "disconnects", "connects", "modbus_errors", "clock_skew")}
STATS.update(connected=False, started=time.time())
now_ms = lambda: int(time.time() * 1000)  # noqa: E731


class Buffer:
    """Persistent-очередь: seq = rowid AUTOINCREMENT (не переиспользуется после удаления)."""

    def __init__(self, path: str, cap: int):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS buf(seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "prio INTEGER NOT NULL, created INTEGER NOT NULL, rec TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v INTEGER)")
        self.lock = threading.Lock()
        self.cap = cap
        self.n = self.db.execute("SELECT count(*) FROM buf").fetchone()[0]
        row = self.db.execute("SELECT v FROM meta WHERE k='dropped'").fetchone()
        STATS["dropped"] = row[0] if row else 0
        log.info("буфер %s: %d записей после старта, ёмкость %d", path, self.n, cap)

    def put(self, rec: dict, prio: int) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO buf(prio, created, rec) VALUES (?, ?, ?)",
                                  (prio, now_ms(), json.dumps(rec, separators=(",", ":"), ensure_ascii=False)))
            self.n += 1
            if self.n > self.cap:
                # политика переполнения: вытесняются самые старые обычные записи, алармы сохраняются
                k = self.n - self.cap
                d = self.db.execute("DELETE FROM buf WHERE seq IN (SELECT seq FROM buf WHERE prio=1 "
                                    "ORDER BY seq LIMIT ?)", (k,)).rowcount
                self.n -= d
                STATS["dropped"] += d
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('dropped', ?)", (STATS["dropped"],))
            return cur.lastrowid

    def take(self, prio: int, limit: int, skip: set[int]) -> list[tuple[int, int, str]]:
        with self.lock:
            rows = self.db.execute("SELECT seq, created, rec FROM buf WHERE prio=? ORDER BY seq LIMIT ?",
                                   (prio, limit + len(skip))).fetchall()
        return [r for r in rows if r[0] not in skip][:limit]

    def delete(self, seqs: list[int]) -> None:
        with self.lock:
            d = self.db.execute(f"DELETE FROM buf WHERE seq IN ({','.join('?' * len(seqs))})", seqs).rowcount
            self.n -= d

    def seq_info(self) -> tuple[int, int | None]:
        """(последний выданный seq, наименьший seq в буфере)."""
        with self.lock:
            last = self.db.execute("SELECT seq FROM sqlite_sequence WHERE name='buf'").fetchone()
            low = self.db.execute("SELECT min(seq) FROM buf").fetchone()[0]
        return (last[0] if last else 0), low

    def size_bytes(self) -> int:
        with self.lock:
            pc = self.db.execute("PRAGMA page_count").fetchone()[0]
            ps = self.db.execute("PRAGMA page_size").fetchone()[0]
        return pc * ps


BUF = Buffer("/data/buffer.db", BUFFER_MAX)
WAKE = threading.Event()
RAW = open("/data/raw.csv", "a", encoding="utf-8", buffering=1) if RAW_LOG else None
FILTERS: dict[tuple[str, str], Deadband] = {}
ALARM_STATE: dict[tuple[str, str], bool] = {}


def emit(asset: str, tag: str, value: float, unit: str, dts: int, spec: dict, quality: list[str]) -> None:
    """Нормализованное значение → правила → deadband → буфер."""
    STATS["samples"] += 1
    gts = now_ms()
    rec = {"gw": GW, "asset": asset, "tag": tag, "v": value, "u": unit, "dts": dts, "gts": gts}
    if quality:
        rec["q"] = quality
    if RAW:
        RAW.write(f"{dts},{asset},{tag},{value}\n")
    for rule in CFG["rules"]:
        if rule["tag"] != tag:
            continue
        key = (asset, rule["name"])
        active = ALARM_STATE.get(key, False)
        if (not active and value > rule["above"]) or (active and value < rule["clear_below"]):
            ALARM_STATE[key] = not active
            alarm = {**rec, "type": "alarm", "rule": rule["name"], "severity": rule["severity"],
                     "state": "raised" if not active else "cleared", "threshold": rule["above"]}
            BUF.put(alarm, prio=0)
            STATS["alarms"] += 1
            log.warning("АЛАРМ %s %s %s = %.1f", alarm["state"], asset, rule["name"], value)
            WAKE.set()
    key = (asset, tag)
    if key not in FILTERS:
        FILTERS[key] = Deadband(DB_MODE, spec.get("deadband_abs", 0), spec.get("deadband_pct", 0),
                                CFG["heartbeat_s"], spec.get("discrete", False), DB_SCALE)
    if FILTERS[key].check(value, dts) is None:
        STATS["filtered"] += 1
        return
    BUF.put(rec, prio=1)
    STATS["enqueued"] += 1


def modbus_driver() -> None:
    mb = CFG["modbus"]
    cli = ModbusTcpClient(mb["host"], port=mb["port"], timeout=2)
    n = len(mb["assets"]) * len(mb["registers"])
    t0 = time.time()
    k = 0
    while True:
        k += 1
        try:
            if not cli.connected:
                cli.connect()
            rr = cli.read_holding_registers(0, count=n, slave=mb["unit"])
            if rr.isError():
                raise IOError(str(rr))
            ts = now_ms()
            for a, asset in enumerate(mb["assets"]):
                for spec in mb["registers"]:
                    raw = rr.registers[a * len(mb["registers"]) + spec["offset"]]
                    if spec["signed"] and raw >= 0x8000:
                        raw -= 0x10000
                    emit(asset, spec["tag"], round(raw * spec["scale"], 3), spec["unit"], ts, spec, [])
        except Exception as e:  # noqa: BLE001 — драйвер не должен падать, ошибка считается и логируется
            STATS["modbus_errors"] += 1
            log.error("modbus: %s", e)
            cli.close()
        time.sleep(max(0.0, t0 + k * mb["poll_s"] - time.time()))


def legacy_driver() -> None:
    lg = CFG["legacy_mqtt"]

    def on_msg(_c, _u, m):
        try:
            d = json.loads(m.payload)
            dts = int(d["ts"] * 1000) if lg["ts_unit"] == "s" else int(d["ts"])
        except (ValueError, KeyError):
            return
        q = []
        if abs(dts - now_ms()) > lg["clock_skew_ms"]:
            q.append("clock_skew")
            STATS["clock_skew"] += 1
        for f in lg["fields"]:
            if f["field"] in d:
                emit(d["id"], f["tag"], round(d[f["field"]] * f["scale"], 3), f["unit"], dts, f, q)

    c = mqtt.Client(V2, client_id=f"{GW}-south")
    c.on_message = on_msg
    c.on_connect = lambda cl, *_: cl.subscribe(lg["topic"], qos=0)
    c.connect(lg["host"], 1883)
    c.loop_forever(retry_first_connection=True)


def publisher() -> None:
    """Отправка из буфера. Новый клиент на каждое подключение: паho не хранит своих копий сообщений."""
    backoff = 1.0
    while True:
        inflight: dict[int, list[int]] = {}
        lock = threading.Lock()
        up = threading.Event()
        down = threading.Event()

        def on_connect(cl, _u, _f, rc, _p):
            if not rc.is_failure:
                up.set()

        def on_publish(_c, _u, mid, _rc, _p):
            with lock:
                seqs = inflight.pop(mid, None)
            if seqs:
                STATS["acked_records"] += len(seqs)
                if ACK_MODE == "puback":
                    BUF.delete(seqs)
            WAKE.set()

        def on_disconnect(*_):
            down.set()
            WAKE.set()

        c = mqtt.Client(V2, client_id=GW, clean_session=True)
        c.will_set(T_STATUS, "offline", qos=1, retain=True)
        c.max_inflight_messages_set(MAX_INFLIGHT)
        c.on_connect, c.on_publish, c.on_disconnect = on_connect, on_publish, on_disconnect
        try:
            c.connect(NORTH, 1883, keepalive=KEEPALIVE)
        except OSError as e:
            log.warning("north: %s, повтор через %.0f с", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 5.0)
            continue
        c.loop_start()
        if not up.wait(5):
            c.loop_stop()
            c.disconnect()
            continue
        backoff = 1.0
        STATS["connects"] += 1
        STATS["connected"] = True
        log.info("north: подключено, в буфере %d записей", BUF.n)
        c.publish(T_STATUS, "online", qos=1, retain=True)
        tokens, last = float(BATCH), time.time()
        while not down.is_set():
            with lock:
                busy = set(s for seqs in inflight.values() for s in seqs)
                room = MAX_INFLIGHT - len(inflight)
            sent = False
            # 1) алармы — немедленно, по одному, без ограничителя
            for seq, _cr, rec in BUF.take(0, max(0, room), busy):
                r = json.loads(rec)
                r["seq"] = seq
                info = c.publish(T_ALARM, json.dumps(r, ensure_ascii=False), qos=1)
                with lock:
                    inflight[info.mid] = [seq]
                if ACK_MODE == "send":
                    BUF.delete([seq])
                STATS["sent_records"] += 1
                STATS["sent_msgs"] += 1
                room -= 1
                sent = True
            # 2) обычные записи пакетами, с ограничением скорости (токен-бакет на записи/с)
            if RATE > 0:
                t = time.time()
                tokens = min(float(BATCH), tokens + RATE * (t - last))
                last = t
            rows = BUF.take(1, BATCH, busy) if room > 0 else []
            ready = rows and (len(rows) >= BATCH or now_ms() - rows[0][1] >= MAX_DELAY * 1000)
            if ready and RATE > 0 and tokens < len(rows):
                WAKE.wait((len(rows) - tokens) / RATE)   # ждём токенов на целый пакет
                WAKE.clear()
                continue
            if ready:
                recs = []
                for seq, _cr, rec in rows:
                    r = json.loads(rec)
                    r["seq"] = seq
                    recs.append(r)
                body = {"gw": GW, "n": len(recs), "dropped_total": STATS["dropped"], "records": recs}
                info = c.publish(T_BATCH, json.dumps(body, separators=(",", ":"), ensure_ascii=False), qos=1)
                seqs = [r["seq"] for r in recs]
                with lock:
                    inflight[info.mid] = seqs
                if ACK_MODE == "send":
                    BUF.delete(seqs)              # ОШИБОЧНЫЙ режим для сравнения: удалить сразу
                STATS["sent_records"] += len(seqs)
                STATS["sent_msgs"] += 1
                if RATE > 0:
                    tokens -= len(seqs)
                sent = True
            if not sent:
                WAKE.wait(0.05)
                WAKE.clear()
        STATS["connected"] = False
        STATS["disconnects"] += 1
        log.warning("north: соединение потеряно, неподтверждено %d сообщений, в буфере %d", len(inflight), BUF.n)
        c.loop_stop()
        try:
            c.disconnect()
        except Exception:  # noqa: BLE001
            pass


class Stats(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        last_seq, low_seq = BUF.seq_info()
        body = {**STATS, "buffer": BUF.n, "buffer_bytes": BUF.size_bytes(), "t": time.time(),
                "last_seq": last_seq, "low_seq": low_seq,
                "config": {"BUFFER_MAX": BUFFER_MAX, "BATCH": BATCH, "BACKFILL_RATE": RATE, "DB_MODE": DB_MODE,
                           "DB_SCALE": DB_SCALE, "ACK_MODE": ACK_MODE}}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    log.info("gw-01: BUFFER_MAX=%d BATCH=%d RATE=%g DB=%s×%g ACK=%s", BUFFER_MAX, BATCH, RATE, DB_MODE, DB_SCALE,
             ACK_MODE)
    for fn in (modbus_driver, legacy_driver, publisher):
        threading.Thread(target=fn, daemon=True, name=fn.__name__).start()
    ThreadingHTTPServer(("0.0.0.0", 8081), Stats).serve_forever()
