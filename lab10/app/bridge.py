"""Мост MQTT → Kafka (Redpanda).

Подписка на $share/bridge/telemetry/# — экземпляры моста делят поток между собой (MQTT 5 shared
subscription), стратегия EMQX `local` отдаёт сообщение мосту на том же узле кластера.
Ключ Kafka = device_id (из топика), поэтому все сообщения одного устройства попадают в одну
партицию и обрабатываются по порядку.
"""
import os
import socket
import time

import paho.mqtt.client as mqtt
from confluent_kafka import Producer
from prometheus_client import Counter, start_http_server

MQTT_HOST = os.environ.get("MQTT_HOST", "nginx")
KAFKA = os.environ.get("KAFKA", "redpanda:9092")
TOPIC = "$share/bridge/telemetry/#"

received = Counter("bridge_received_total", "сообщений получено из MQTT")
produced = Counter("bridge_produced_total", "сообщений подтверждено Kafka")
failed = Counter("bridge_failed_total", "ошибок доставки в Kafka")

producer = Producer({
    "bootstrap.servers": KAFKA,
    "linger.ms": 5,
    "batch.num.messages": 10000,
    "queue.buffering.max.messages": 1_000_000,
    "enable.idempotence": True,          # acks=all + без дублей при повторе внутри продюсера
})


def on_delivery(err, _msg):
    (failed if err else produced).inc()


def on_connect(client, _ud, _flags, reason, _props):
    print(f"MQTT connected: {reason}", flush=True)
    client.subscribe(TOPIC, qos=1)


def on_message(_client, _ud, msg):
    received.inc()
    key = msg.topic.split("/", 1)[1]
    while True:
        try:
            producer.produce("telemetry", value=msg.payload, key=key, on_delivery=on_delivery)
            break
        except BufferError:              # локальная очередь продюсера полна — ждём Kafka
            producer.poll(0.05)
    producer.poll(0)


def main():
    start_http_server(8001)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"bridge-{socket.gethostname()}",
                         protocol=mqtt.MQTTv5)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(1, 5)
    while True:
        try:
            client.connect(MQTT_HOST, 1883, keepalive=30)
            break
        except OSError as e:
            print(f"MQTT connect failed: {e}", flush=True)
            time.sleep(2)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
