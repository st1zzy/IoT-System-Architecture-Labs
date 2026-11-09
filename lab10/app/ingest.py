"""Ingest: валидация → обогащение → пакетная запись (COPY) в TimescaleDB.

MODE=kafka — член consumer group "ingest" топика telemetry; offset фиксируется только после
            записи пакета в БД (at-least-once). Невалидные сообщения — в telemetry.dlq.
MODE=mqtt  — базовый вариант без буфера: подписка прямо на брокер (MQTT_TOPIC).
"""
import json
import os
import signal
import sys
import socket
import threading
import time

import psycopg
from prometheus_client import Counter, Gauge, start_http_server

MODE = os.environ.get("MODE", "kafka")
PG_DSN = os.environ["PG_DSN"]
MAX_BATCH = int(os.environ.get("MAX_BATCH", "2000"))
MAX_DELAY = float(os.environ.get("MAX_DELAY", "0.1"))
SITES = ["astana-north", "astana-south", "almaty", "karaganda", "shymkent", "aktobe"]
COLS = "run, device_id, seq, ts_ms, recv_ms, temp, hum, bat, site, kafka_ms"

m_in = Counter("ingest_messages_total", "получено сообщений")
m_rows = Counter("ingest_rows_total", "записано строк в БД")
m_bad = Counter("ingest_invalid_total", "невалидных сообщений (DLQ)")
m_batch = Gauge("ingest_last_batch_size", "размер последнего пакета")


def transform(raw: bytes, recv_ms: int, kafka_ms=None):
    """Разбор и проверка одного сообщения; None — невалидное."""
    try:
        m = json.loads(raw)
        d, s, t = m["d"], m["s"], m["t"]
        temp, hum, bat = float(m["temp"]), float(m["hum"]), float(m["bat"])
    except (ValueError, KeyError, TypeError):
        return None
    if not (isinstance(d, str) and isinstance(s, int) and isinstance(t, int)):
        return None
    if not (-40 <= temp <= 85 and 0 <= hum <= 100 and 0 <= bat <= 5):
        return None
    site = SITES[sum(d.encode()) % len(SITES)]     # обогащение: справочник «устройство → объект»
    return (m.get("r", ""), d, s, t, recv_ms, temp, hum, bat, site, kafka_ms)


def write(conn, rows):
    with conn.cursor() as cur, cur.copy(f"COPY telemetry ({COLS}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)
    conn.commit()
    m_rows.inc(len(rows))
    m_batch.set(len(rows))


def connect_db():
    while True:
        try:
            return psycopg.connect(PG_DSN)
        except psycopg.OperationalError as e:
            print(f"DB not ready: {e}", flush=True)
            time.sleep(2)


def run_kafka(conn):
    from confluent_kafka import Consumer, Producer
    c = Consumer({
        "bootstrap.servers": os.environ.get("KAFKA", "redpanda:9092"),
        "group.id": "ingest",
        "client.id": socket.gethostname(),
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
        "partition.assignment.strategy": "cooperative-sticky",
        "fetch.min.bytes": 1,
        "fetch.wait.max.ms": 50,
    })
    dlq = Producer({"bootstrap.servers": os.environ.get("KAFKA", "redpanda:9092")})
    c.subscribe(["telemetry"], on_assign=lambda _c, p: print(f"assigned: {[x.partition for x in p]}", flush=True))
    rows, deadline = [], None
    try:
        _consume_loop(c, dlq, conn, rows, deadline)
    finally:
        c.close()           # штатный выход из группы — ребаланс сразу, без session.timeout


def _consume_loop(c, dlq, conn, rows, deadline):
    while True:
        msgs = c.consume(num_messages=MAX_BATCH, timeout=0.05)
        now = int(time.time() * 1000)
        for msg in msgs:
            if msg.error():
                continue
            m_in.inc()
            r = transform(msg.value(), now, msg.timestamp()[1])   # CreateTime: момент produce() в мосте
            if r is None:
                m_bad.inc()
                dlq.produce("telemetry.dlq", msg.value(), key=msg.key())
            else:
                rows.append(r)
        if rows and deadline is None:
            deadline = time.time() + MAX_DELAY
        if rows and (len(rows) >= MAX_BATCH or time.time() >= deadline):
            write(conn, rows)
            c.commit(asynchronous=True)     # offset — только после записи в БД
            rows, deadline = [], None
        dlq.poll(0)


def run_mqtt(conn):
    """Разбор — прямо в сетевом потоке paho; основной поток только пишет пакеты в БД.
    (Очередь «сырых» байт + разбор в другом потоке душит сетевой поток через GIL.)"""
    import paho.mqtt.client as mqtt
    rows = []
    lock = threading.Lock()

    def on_connect(client, _ud, _flags, reason, _props):
        print(f"MQTT connected: {reason}", flush=True)
        client.subscribe(os.environ["MQTT_TOPIC"], qos=1)

    def on_message(_c, _ud, msg):
        m_in.inc()
        r = transform(msg.payload, int(time.time() * 1000))
        if r is None:
            m_bad.inc()
            return
        with lock:
            rows.append(r)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"ingest-{socket.gethostname()}",
                         protocol=mqtt.MQTTv5)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(1, 5)
    client.connect_async(os.environ.get("MQTT_HOST", "emqx1"), 1883, keepalive=30)
    threading.Thread(target=client.loop_forever, kwargs={"retry_first_connection": True}, daemon=True).start()
    while True:
        time.sleep(MAX_DELAY)
        with lock:
            batch, rows[:] = rows[:], []
        for i in range(0, len(batch), MAX_BATCH):
            write(conn, batch[i:i + MAX_BATCH])


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    start_http_server(8000)
    conn = connect_db()
    print(f"ingest started, mode={MODE}", flush=True)
    (run_kafka if MODE == "kafka" else run_mqtt)(conn)


if __name__ == "__main__":
    main()
