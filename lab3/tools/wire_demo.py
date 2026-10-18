"""Последовательность для захвата пакетов: CONNECT, PUBLISH с QoS 0/1/2 одного и того же
сообщения телеметрии (паузы по 1 с разделяют обмены во времени), DISCONNECT."""

import json
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).parent))
from topics import topic  # noqa: E402

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="dev-001")
c.connect("lab3-mosquitto", 1883, keepalive=60)
c.loop_start()
time.sleep(1)
payload = json.dumps({"seq": 1, "ts": 1792310400000, "value": 21.53, "unit": "°C"},
                     separators=(",", ":"), ensure_ascii=False)
for q in (0, 1, 2):
    c.publish(topic("dev-001", "temperature"), payload, qos=q).wait_for_publish()
    time.sleep(1)
c.loop_stop()
c.disconnect()
print(f"topic {topic('dev-001', 'temperature')} ({len(topic('dev-001', 'temperature'))} Б), "
      f"payload {payload} ({len(payload.encode())} Б)")
