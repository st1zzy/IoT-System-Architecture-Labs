"""Виртуальное устройство → MQTT.

sampler: опрос строго по расписанию → валидация → кольцевой буфер (не зависит от сети);
uplink:  соединение с переподключением; кодирование и отправка буфера, удаление после PUBACK.
"""

import asyncio
import logging
import os
import random
import time

import aiomqtt

from codec import ENCODERS, encode_cbor, encode_json
from device import Device

BROKER = os.getenv("MQTT_HOST", "mosquitto")
PORT = int(os.getenv("MQTT_PORT", "1883"))
DEVICE_ID = os.getenv("DEVICE_ID", "room-sensor-01")
SITE = os.getenv("SITE", "site1")
INTERVAL = float(os.getenv("INTERVAL", "5"))
QOS = int(os.getenv("QOS", "1"))
ENCODING = os.getenv("ENCODING", "json")
BUFFER_SIZE = int(os.getenv("BUFFER_SIZE", "500"))
KEEPALIVE = int(os.getenv("KEEPALIVE", "15"))
BACKOFF_MAX = float(os.getenv("BACKOFF_MAX", "30"))
PUBLISH_TIMEOUT = float(os.getenv("PUBLISH_TIMEOUT", "10"))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("device")


async def sampler(dev: Device, wakeup: asyncio.Event) -> None:
    next_t = time.time()
    while True:
        await asyncio.sleep(max(0.0, next_t - time.time()))
        msg = dev.sample(time.time())
        if "quality" in msg:
            log.info("validation seq=%d flags=%s", msg["seq"], msg["quality"])
        evicted = dev.enqueue(msg)
        if evicted is not None:
            log.warning("buffer full %d/%d: dropped seq=%d (drop_oldest, dropped=%d)",
                        len(dev.buffer), dev.buffer.maxlen, evicted["seq"], dev.dropped)
        wakeup.set()
        next_t += dev.interval


async def uplink(dev: Device, wakeup: asyncio.Event) -> None:
    topic = f"enu/{dev.site}/{dev.id}/up/telemetry/{ENCODING}"
    status = f"enu/{dev.site}/{dev.id}/status"
    encode = ENCODERS[ENCODING]
    backoff = 1.0
    while True:
        try:
            async with aiomqtt.Client(
                BROKER, PORT, identifier=dev.id, keepalive=KEEPALIVE,
                will=aiomqtt.Will(status, b"offline", qos=1, retain=True),
            ) as client:
                log.info("connected to %s:%d, buffered=%d", BROKER, PORT, len(dev.buffer))
                backoff = 1.0
                await client.publish(status, b"online", qos=1, retain=True)
                while True:
                    while dev.buffer:
                        msg = dev.buffer[0]
                        payload = encode(msg)
                        await client.publish(topic, payload, qos=QOS, timeout=PUBLISH_TIMEOUT)
                        dev.buffer.popleft()             # удаляем только после PUBACK
                        log.info("pub seq=%d enc=%s bytes=%d (json=%d cbor=%d) buf=%d/%d",
                                 msg["seq"], ENCODING, len(payload), len(encode_json(msg)),
                                 len(encode_cbor(msg)), len(dev.buffer), dev.buffer.maxlen)
                    wakeup.clear()
                    await wakeup.wait()
        except aiomqtt.MqttError as e:
            log.warning("disconnected: %s; retry in %.0fs, buffered=%d/%d",
                        e, backoff, len(dev.buffer), dev.buffer.maxlen)
            await asyncio.sleep(backoff * random.uniform(0.8, 1.2))
            backoff = min(BACKOFF_MAX, backoff * 2)


async def main() -> None:
    dev = Device(DEVICE_ID, site=SITE, interval=INTERVAL, buffer_size=BUFFER_SIZE)
    log.info("device %s: interval=%.0fs qos=%d encoding=%s buffer=%d (drop_oldest)",
             dev.id, INTERVAL, QOS, ENCODING, BUFFER_SIZE)
    wakeup = asyncio.Event()
    await asyncio.gather(sampler(dev, wakeup), uplink(dev, wakeup))


if __name__ == "__main__":
    asyncio.run(main())
