"""Модель шлюза + Network Server → MQTT стенда курса.

Час работы соты (N устройств, ADR) моделируется заранее, затем принятые шлюзом кадры
публикуются по порядку как uplink-события в формате ChirpStack v4:
    application/<app>/device/<devEui>/event/up
Потерянные в эфире кадры не публикуются — приложение видит их только как разрывы fCnt.

    python tools/ns_bridge.py --n 1000 --hours 1
"""

import argparse
import base64
import json
import os
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).parent))
from lorasim import adr_sf, place, rx_power, simulate  # noqa: E402

APP = "lab-s1"
NOISE_DBM = -174 + 10 * np.log10(125e3) + 6           # шум полосы 125 кГц + NF 6 дБ ≈ −117 дБм
T0 = datetime(2026, 10, 18, tzinfo=timezone.utc).timestamp()


def payload(dev: int, fcnt: int) -> tuple[bytes, dict]:
    """7 Б: температура (сотые °C, int16), влажность (%, uint8), давление (десятые гПа, uint16),
    батарея (%, uint8), флаги (uint8) — двоичная упаковка измерений Lab 1."""
    rng = np.random.default_rng(dev * 100_003 + fcnt)
    t, h, p = 21 + rng.normal(0, 2), 45 + rng.normal(0, 5), 972 + rng.normal(0, 1.5)
    raw = struct.pack("!hBHBB", round(t * 100), round(h), round(p * 10), 87, 0)
    return raw, {"temperature": round(t, 2), "humidity": round(h), "pressure": round(p, 1),
                 "battery": 87}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--period", type=float, default=600)
    ap.add_argument("--hours", type=float, default=1)
    ap.add_argument("--host", default=os.getenv("MQTT_HOST", "localhost"))
    ap.add_argument("--rate", type=float, default=2000, help="публикаций в секунду (ускорение времени)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    rng = np.random.default_rng(42)
    pw = rx_power(place(args.n, 5.0, rng), 6.0, rng)
    sf = adr_sf(pw)
    res, pk = simulate(args.n, sf, args.period, duration_s=args.hours * 3600, power=pw, seed=42,
                       packets=True)
    order = np.argsort(pk["start"])
    fcnt = np.zeros(args.n, int)
    frames = []
    for i in order:                               # fCnt растёт на каждой передаче, даже потерянной
        d = int(pk["dev"][i])
        frames.append((i, d, int(fcnt[d])))
        fcnt[d] += 1
    print(f"модель: {args.n} устройств, {args.hours:g} ч, отправлено {res.sent}, принято шлюзом "
          f"{res.delivered} (PDR {100 * res.pdr:.1f} %: вне покрытия {res.no_coverage}, "
          f"демодуляторы {res.demod_drop}, коллизии {res.collided}, захват спас {res.captured})")

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="lab-s1-network-server")
    c.connect(args.host, 1883)
    c.loop_start()
    pub, t0 = 0, time.time()
    for i, d, fc in frames:
        if not pk["delivered"][i]:
            continue
        raw, obj = payload(d, fc)
        s = int(pk["sf"][i])
        rssi = float(pk["rssi"][i])
        ev = {
            "time": datetime.fromtimestamp(T0 + float(pk["start"][i]), timezone.utc).isoformat(),
            "deviceInfo": {"applicationName": APP, "devEui": f"70b3d5{d:010x}", "deviceName": f"node-{d:04d}"},
            "fCnt": fc, "fPort": 1, "dr": 12 - s, "data": base64.b64encode(raw).decode(), "object": obj,
            "rxInfo": [{"gatewayId": "aa555a0000000101", "rssi": round(rssi), "snr": round(rssi - NOISE_DBM, 1)}],
            "txInfo": {"frequency": 868_100_000 + 200_000 * int(pk["ch"][i]),
                       "modulation": {"lora": {"bandwidth": 125000, "spreadingFactor": s,
                                               "codeRate": "CR_4_5"}}},
        }
        c.publish(f"application/{APP}/device/{ev['deviceInfo']['devEui']}/event/up", json.dumps(ev), qos=1)
        pub += 1
        time.sleep(max(0.0, t0 + pub / args.rate - time.time()))
    time.sleep(1)
    c.loop_stop()
    c.disconnect()
    print(f"опубликовано в MQTT: {pub} uplink-событий за {time.time() - t0:.1f} с "
          f"(application/{APP}/device/+/event/up)")


if __name__ == "__main__":
    main()
