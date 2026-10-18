"""Коллектор — приёмная сторона для проверки уровня устройства.

Декодирует JSON/CBOR, валидирует по JSON Schema, контролирует непрерывность seq,
добавляет ingest_ts и пишет каждое сообщение в CSV. Сводка — в лог и summary.json.
"""

import asyncio
import csv
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone

import aiomqtt
import cbor2
from jsonschema import Draft202012Validator

BROKER = os.getenv("MQTT_HOST", "mosquitto")
PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC = os.getenv("TOPIC", "enu/+/+/up/telemetry/+")
SCHEMA = os.getenv("SCHEMA", "/schema/telemetry.schema.json")
RUN = os.getenv("RUN", "main")
OUT = os.path.join(os.getenv("OUT_DIR", "/data"), RUN)
SUMMARY_EVERY = float(os.getenv("SUMMARY_EVERY", "60"))

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collector")
DECODERS = {"json": json.loads, "cbor": cbor2.loads}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds")


class Stats:
    def __init__(self) -> None:
        self.rx = self.valid = self.invalid = self.dup = self.reorder = 0
        self.seen: dict[str, set[int]] = {}
        self.expected: dict[str, int] = {}
        self.max_delay = 0.0

    def track(self, dev: str, seq: int) -> str:
        seen = self.seen.setdefault(dev, set())
        exp = self.expected.get(dev)
        if seq in seen:
            self.dup += 1
            return "dup"
        seen.add(seq)
        if exp is None or seq == exp:
            self.expected[dev] = seq + 1
            return "ok"
        if seq > exp:
            self.expected[dev] = seq + 1
            return f"gap({seq - exp})"
        self.reorder += 1
        return "late"

    def summary(self) -> dict:
        devs = {}
        for dev, seen in self.seen.items():
            lo, hi = min(seen), max(seen)
            devs[dev] = {"seq_min": lo, "seq_max": hi, "unique": len(seen),
                         "missing": hi - lo + 1 - len(seen)}
        return {"rx": self.rx, "valid": self.valid, "invalid": self.invalid, "dup": self.dup,
                "late": self.reorder, "max_delay_s": round(self.max_delay, 3), "devices": devs}


async def main() -> None:
    validator = Draft202012Validator(json.load(open(SCHEMA, encoding="utf-8")))
    os.makedirs(OUT, exist_ok=True)
    f = open(os.path.join(OUT, "received.csv"), "a", newline="", encoding="utf-8")
    w = csv.writer(f)
    if f.tell() == 0:
        w.writerow(["ingest_ts", "device_ts", "delay_s", "device_id", "seq", "encoding", "bytes",
                    "seq_check", "valid", "error", "temperature", "humidity", "pressure", "motion",
                    "quality"])
    st = Stats()

    def dump(reason: str) -> None:
        s = st.summary()
        with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as fs:
            json.dump(s, fs, indent=2)
        for dev, d in s["devices"].items():
            log.info("summary(%s) %s: rx=%d valid=%d invalid=%d seq=%d..%d missing=%d dup=%d "
                     "late=%d max_delay=%.1fs", reason, dev, s["rx"], s["valid"], s["invalid"],
                     d["seq_min"], d["seq_max"], d["missing"], s["dup"], s["late"],
                     s["max_delay_s"])

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop.set)

    async def periodic() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), SUMMARY_EVERY)
            except asyncio.TimeoutError:
                dump("periodic")

    async def consume() -> None:
        while True:
            try:
                # persistent-сессия: сообщения, пришедшие пока коллектор офлайн, ждут в брокере
                async with aiomqtt.Client(BROKER, PORT, identifier=f"lab1-collector-{RUN}",
                                          clean_session=False) as client:
                    await client.subscribe(TOPIC, qos=1)
                    log.info("subscribed %s", TOPIC)
                    async for m in client.messages:
                        handle(str(m.topic), m.payload)
            except aiomqtt.MqttError as e:
                log.warning("disconnected: %s; retry in 2s", e)
                await asyncio.sleep(2)

    def handle(topic: str, payload: bytes) -> None:
        ingest = time.time()
        enc = topic.rsplit("/", 1)[-1]
        st.rx += 1
        try:
            msg = DECODERS[enc](payload)
        except Exception as e:  # noqa: BLE001 — любой мусор на входе фиксируем как invalid
            st.invalid += 1
            log.warning("undecodable %s payload (%d B): %s", enc, len(payload), e)
            return
        errors = sorted(validator.iter_errors(msg), key=lambda e: list(e.path))
        if errors:
            st.invalid += 1
            err = "; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors)
            log.warning("schema invalid seq=%s: %s", msg.get("seq"), err)
        else:
            st.valid += 1
            err = ""
        dev, seq = msg.get("device_id", "?"), msg.get("seq", -1)
        check = st.track(dev, seq) if isinstance(seq, int) else "no_seq"
        delay = ingest - msg["ts"] / 1000 if isinstance(msg.get("ts"), int) else None
        if delay is not None:
            st.max_delay = max(st.max_delay, delay)
        m = msg.get("metrics", {})
        w.writerow([iso(ingest), iso(msg["ts"] / 1000) if delay is not None else "",
                    f"{delay:.3f}" if delay is not None else "", dev, seq, enc, len(payload), check,
                    not errors, err, m.get("temperature"), m.get("humidity"), m.get("pressure"),
                    m.get("motion"), json.dumps(msg.get("quality", {}), ensure_ascii=False)])
        f.flush()
        level = logging.WARNING if check != "ok" else logging.INFO
        log.log(level, "rx %s seq=%s enc=%s bytes=%d delay=%.3fs schema=%s seq_check=%s",
                dev, seq, enc, len(payload), delay or 0, "ok" if not errors else "INVALID", check)

    consumer = asyncio.create_task(consume())
    await periodic()
    consumer.cancel()
    dump("final")
    f.close()


if __name__ == "__main__":
    asyncio.run(main())
