"""Наполнение топика telemetry для опыта с consumer group: N сообщений того же формата, что у устройств,
ключ = device_id (10 000 устройств → равномерно по 8 партициям). Пишет прямо в Kafka, минуя MQTT:
опыт измеряет только скорость разбора очереди группой consumer'ов.

    python /app/fill_kafka.py --messages 3000000
"""
import argparse
import random
import time

from confluent_kafka import Producer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--messages", type=int, default=3_000_000)
    ap.add_argument("--devices", type=int, default=10_000)
    ap.add_argument("--kafka", default="redpanda:9092")
    a = ap.parse_args()
    p = Producer({"bootstrap.servers": a.kafka, "linger.ms": 20, "batch.num.messages": 10000,
                  "queue.buffering.max.messages": 2_000_000, "compression.type": "none"})
    seq = [0] * a.devices
    t0 = time.time()
    for i in range(a.messages):
        d = i % a.devices
        cid = f"drain-{d:05d}"
        ms = int(time.time() * 1000)
        v = (f'{{"r":"drain","d":"{cid}","s":{seq[d]},"t":{ms},"temp":{random.uniform(18, 26):.2f},'
             f'"hum":{random.uniform(30, 60):.1f},"bat":{random.uniform(3.4, 4.2):.3f}}}')
        seq[d] += 1
        while True:
            try:
                p.produce("telemetry", v.encode(), key=cid)
                break
            except BufferError:
                p.poll(0.05)
        if i % 10000 == 0:
            p.poll(0)
    p.flush()
    el = time.time() - t0
    print(f"produced {a.messages} messages in {el:.1f}s ({a.messages / el:,.0f} msg/s)")


if __name__ == "__main__":
    main()
