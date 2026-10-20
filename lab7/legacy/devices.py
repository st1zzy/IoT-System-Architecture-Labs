"""10 «старых» MQTT-устройств южной стороны со своим форматом данных.

Топик legacy/env-XX/state, JSON {"id", "t": температура ×10 °C, "rh": влажность ×10 %, "ts": секунды UNIX}.
У env-07 часы спешат на 7 с — шлюз должен это заметить.
"""

import json
import math
import os
import random
import time

import paho.mqtt.client as mqtt

N = int(os.getenv("DEVICES", "10"))
SKEW = {"env-07": 7.0}

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="legacy-sim")
c.connect(os.getenv("MQTT_HOST", "lab7-south"), 1883)
c.loop_start()
temp = {f"env-{i:02d}": 21.0 + 0.3 * i for i in range(1, N + 1)}
t0 = time.time()
k = 0
while True:
    for dev in temp:
        t = time.time() - t0
        temp[dev] += 0.02 * (21.0 + 2 * math.sin(2 * math.pi * t / 900) - temp[dev]) + random.gauss(0, 0.05)
        rh = 45 - 1.5 * (temp[dev] - 21) + random.gauss(0, 0.4)
        msg = {"id": dev, "t": round(temp[dev] * 10), "rh": round(rh * 10), "ts": int(time.time() + SKEW.get(dev, 0))}
        c.publish(f"legacy/{dev}/state", json.dumps(msg), qos=0)
    k += 1
    time.sleep(max(0.0, t0 + k - time.time()))
