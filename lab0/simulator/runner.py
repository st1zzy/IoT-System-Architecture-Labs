"""Пул виртуальных устройств → MQTT.

Каждое устройство — две корутины:
  sampler: опрашивает датчики строго по расписанию и кладёт сообщение в буфер;
  uplink:  держит MQTT-соединение (с переподключением) и выгружает буфер.
Опрос не зависит от состояния сети: при недоступности брокера данные
копятся в буфере и досылаются после переподключения.
"""

import asyncio
import json
import logging
import os
import random
import time

import aiomqtt

from device import Device

BROKER = os.getenv("MQTT_HOST", "mosquitto")
PORT = int(os.getenv("MQTT_PORT", "1883"))
N = int(os.getenv("DEVICES", "1"))
INTERVAL = float(os.getenv("INTERVAL", "5"))
QOS = int(os.getenv("QOS", "1"))
JITTER = float(os.getenv("JITTER", "0.0"))      # доля интервала
KEEPALIVE = int(os.getenv("KEEPALIVE", "15"))
METRICS = [m for m in os.getenv("METRICS", "").split(",") if m] or None
BUFFER_SIZE = int(os.getenv("BUFFER_SIZE", "500"))
BACKOFF_MAX = float(os.getenv("BACKOFF_MAX", "30"))
PUBLISH_TIMEOUT = float(os.getenv("PUBLISH_TIMEOUT", "10"))   # ожидание PUBACK, с

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sim")


def telemetry_topic(dev: Device) -> str:
    return f"enu/{dev.site}/{dev.id}/up/telemetry"


def status_topic(dev: Device) -> str:
    return f"enu/{dev.site}/{dev.id}/status"


async def sampler(dev: Device, wakeup: asyncio.Event) -> None:
    next_t = time.time() + random.uniform(0, dev.interval * JITTER)
    while True:
        await asyncio.sleep(max(0.0, next_t - time.time()))
        dev.enqueue(dev.sample(time.time()))
        wakeup.set()
        next_t += dev.interval * (1 + random.uniform(-JITTER, JITTER))


async def uplink(dev: Device, wakeup: asyncio.Event) -> None:
    backoff = 1.0
    while True:
        try:
            async with aiomqtt.Client(
                BROKER, PORT,
                identifier=dev.id,
                keepalive=KEEPALIVE,
                will=aiomqtt.Will(status_topic(dev), b"offline", qos=1, retain=True),
            ) as client:
                log.info("%s connected to %s:%d (buffered=%d)",
                         dev.id, BROKER, PORT, len(dev.buffer))
                backoff = 1.0
                await client.publish(status_topic(dev), b"online", qos=1, retain=True)
                while True:
                    while dev.buffer:
                        msg = dev.buffer[0]
                        payload = json.dumps(msg, separators=(",", ":")).encode()
                        await client.publish(telemetry_topic(dev), payload, qos=QOS,
                                             timeout=PUBLISH_TIMEOUT)
                        dev.buffer.popleft()        # удаляем только после успешной отправки
                        log.info("%s pub seq=%d bytes=%d buffered=%d dropped=%d",
                                 dev.id, msg["seq"], len(payload),
                                 len(dev.buffer), dev.dropped)
                    wakeup.clear()
                    await wakeup.wait()
        except aiomqtt.MqttError as e:
            log.warning("%s disconnected: %s; retry in %.0fs (buffered=%d)",
                        dev.id, e, backoff, len(dev.buffer))
            await asyncio.sleep(backoff * random.uniform(0.8, 1.2))
            backoff = min(BACKOFF_MAX, backoff * 2)


async def main() -> None:
    devices = [Device(f"dev-{i:06d}", site=f"site{i % 4 + 1}", interval=INTERVAL,
                      metrics=METRICS, buffer_size=BUFFER_SIZE) for i in range(N)]
    log.info("starting %d device(s), interval=%.1fs, qos=%d, metrics=%s",
             N, INTERVAL, QOS, list(devices[0].sensors))
    tasks = []
    for d in devices:
        ev = asyncio.Event()
        tasks += [sampler(d, ev), uplink(d, ev)]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
