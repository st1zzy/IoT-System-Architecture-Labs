"""Ingest-сервис телеметрии: MQTT → validate → enrich → batch → SQLite, метрики Prometheus.

Поток paho принимает сообщения и кладёт их в очередь; рабочий поток валидирует, обогащает,
собирает батч (MAX_BATCH сообщений или MAX_DELAY секунд) и пишет его одной транзакцией.
BACKPRESSURE=1: PUBACK отправляется только после записи (manual ack), поэтому брокер не шлёт
больше max_inflight_messages неподтверждённых — излишек ждёт в очереди брокера.
"""

import json
import os
import queue
import sqlite3
import threading
import time
from collections import deque

import paho.mqtt.client as mqtt
import uvicorn
from fastapi import FastAPI, Response
from jsonschema import Draft202012Validator
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

BROKER = os.getenv("MQTT_HOST", "lab5-mosquitto")
TOPIC = os.getenv("TOPIC", "enu/+/+/up/telemetry")
MAX_BATCH = int(os.getenv("MAX_BATCH", "500"))
MAX_DELAY = float(os.getenv("MAX_DELAY", "2.0"))
BACKPRESSURE = os.getenv("BACKPRESSURE", "0") == "1"
DB = os.getenv("DB", "/data/telemetry.db")

RECEIVED = Counter("messages_received_total", "Сообщений получено из MQTT")
STORED = Counter("messages_stored_total", "Сообщений записано в хранилище")
INVALID = Counter("messages_invalid_total", "Сообщений не прошло JSON Schema")
BATCH = Histogram("batch_size", "Размер записанного батча",
                  buckets=(1, 5, 10, 50, 100, 200, 300, 400, 500, 1000))
LATENCY = Histogram("processing_latency_seconds", "Задержка от device_ts до записи",
                    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30, 60, 120))
QUEUE = Gauge("queue_depth", "Сообщений в очереди сервиса")

validator = Draft202012Validator(json.load(open("telemetry.schema.json", encoding="utf-8")))
q: "queue.Queue[tuple[mqtt.MQTTMessage, float]]" = queue.Queue()
window = {"lat": deque(maxlen=500_000), "batches": deque(maxlen=100_000), "qmax": 0, "t0": time.time(),
          "stored": 0, "received": 0}
lock = threading.Lock()


def on_message(_c, _u, m):
    RECEIVED.inc()
    with lock:
        window["received"] += 1
    q.put((m, time.time()))


def worker(client: mqtt.Client) -> None:
    db = sqlite3.connect(DB, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("CREATE TABLE IF NOT EXISTS telemetry (ts INTEGER, ingest_ts REAL, device_id TEXT, "
               "site TEXT, metric TEXT, value REAL, seq INTEGER)")
    rows, msgs, first = [], [], None
    while True:
        depth = q.qsize()
        QUEUE.set(depth)
        with lock:
            window["qmax"] = max(window["qmax"], depth)
        timeout = max(0.0, first + MAX_DELAY - time.time()) if msgs else 0.5
        try:
            m, t_rx = q.get(timeout=timeout)
            try:
                msg = json.loads(m.payload)
                if not validator.is_valid(msg):
                    raise ValueError
            except ValueError:
                INVALID.inc()
                if BACKPRESSURE:
                    client.ack(m.mid, m.qos)
                continue
            _, site, dev, *_ = m.topic.split("/")                       # обогащение из топика
            for k, v in msg["metrics"].items():
                rows.append((msg["ts"], t_rx, dev, site, k, None if v is None else float(v), msg["seq"]))
            msgs.append((m, msg["ts"]))
            first = first or time.time()
        except queue.Empty:
            pass
        if msgs and (len(msgs) >= MAX_BATCH or time.time() - first >= MAX_DELAY):
            db.executemany("INSERT INTO telemetry VALUES (?,?,?,?,?,?,?)", rows)
            db.commit()                                                  # одна транзакция на батч
            now = time.time()
            lat = [now - ts / 1000 for _, ts in msgs]
            for x in lat:
                LATENCY.observe(x)
            BATCH.observe(len(msgs))
            STORED.inc(len(msgs))
            with lock:
                window["lat"].extend(lat)
                window["batches"].append(len(msgs))
                window["stored"] += len(msgs)
            if BACKPRESSURE:
                for m, _ in msgs:
                    client.ack(m.mid, m.qos)
            rows, msgs, first = [], [], None


app = FastAPI(title="lab5-ingest")


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "backpressure": BACKPRESSURE, "max_batch": MAX_BATCH, "max_delay": MAX_DELAY}


@app.post("/reset")
def reset() -> dict:
    with lock:
        window["lat"].clear(); window["batches"].clear()
        window.update(qmax=q.qsize(), t0=time.time(), stored=0, received=0)
    return {"ok": True}


@app.get("/stats")
def stats() -> dict:
    with lock:
        lat = sorted(window["lat"])
        b = list(window["batches"])
        dt = time.time() - window["t0"]
        p = lambda x: lat[min(len(lat) - 1, int(x * (len(lat) - 1)))] if lat else None  # noqa: E731
        return {"seconds": dt, "received": window["received"], "stored": window["stored"],
                "throughput": window["stored"] / dt if dt else 0, "queue_depth": q.qsize(),
                "queue_max": window["qmax"], "p50": p(0.5), "p95": p(0.95), "p99": p(0.99),
                "batches": len(b), "batch_mean": sum(b) / len(b) if b else 0, "batch_max": max(b) if b else 0}


def main() -> None:
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="lab5-ingest", manual_ack=BACKPRESSURE)
    c.on_message = on_message
    c.on_connect = lambda cl, *_: cl.subscribe(f"{TOPIC}", qos=1)
    c.connect(BROKER, 1883, keepalive=60)
    c.loop_start()
    threading.Thread(target=worker, args=(c,), daemon=True).start()
    print(f"ingest: batch={MAX_BATCH} delay={MAX_DELAY}s backpressure={BACKPRESSURE}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
