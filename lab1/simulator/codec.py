"""Кодирование сообщения: JSON, CBOR и компактный CBOR.

cbor        — та же структура, что и JSON (RFC 8949): строковые ключи, float64 для дробных.
cbor_compact — целочисленные ключи + значения в масштабированных целых (сотые °C, десятые %RH,
               сотые гПа). Требует общего словаря ключей на устройстве и платформе.
"""

import json

import cbor2

CHANNELS = ["temperature", "humidity", "pressure", "motion"]
SCALE = {"temperature": 100, "humidity": 10, "pressure": 100}
FLAGS = ["nan", "out_of_range", "spike", "stuck"]
# словарь ключей компактного формата: 0..23 кодируются одним байтом
K_VER, K_DEV, K_TS, K_SEQ, K_MET, K_BAT, K_RSSI, K_Q = range(8)


def encode_json(msg: dict) -> bytes:
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode()


def encode_json_pretty(msg: dict) -> bytes:
    return json.dumps(msg, ensure_ascii=False).encode()        # разделители по умолчанию: ", " и ": "


def encode_cbor(msg: dict) -> bytes:
    return cbor2.dumps(msg)


def encode_cbor_compact(msg: dict) -> bytes:
    m = msg["metrics"]
    metrics = [None if m[c] is None else round(m[c] * SCALE[c]) for c in CHANNELS[:3]] + [m["motion"]]
    out = {K_VER: msg["schema_version"], K_DEV: msg["device_id"], K_TS: msg["ts"],
           K_SEQ: msg["seq"], K_MET: metrics}
    if "battery" in msg:
        out[K_BAT] = round(msg["battery"] * 10)
    if "rssi" in msg:
        out[K_RSSI] = msg["rssi"]
    if "quality" in msg:      # канал → битовая маска флагов
        out[K_Q] = {CHANNELS.index(c): sum(1 << FLAGS.index(f) for f in fl)
                    for c, fl in msg["quality"].items()}
    return cbor2.dumps(out)


def decode_cbor_compact(data: bytes) -> dict:
    d = cbor2.loads(data)
    vals = d[K_MET]
    metrics = {c: None if vals[i] is None else vals[i] / SCALE[c] for i, c in enumerate(CHANNELS[:3])}
    metrics["motion"] = vals[3]
    msg = {"schema_version": d[K_VER], "device_id": d[K_DEV], "ts": d[K_TS], "seq": d[K_SEQ],
           "metrics": metrics}
    if K_BAT in d:
        msg["battery"] = d[K_BAT] / 10
    if K_RSSI in d:
        msg["rssi"] = d[K_RSSI]
    if K_Q in d:
        msg["quality"] = {CHANNELS[i]: [f for j, f in enumerate(FLAGS) if mask >> j & 1]
                          for i, mask in d[K_Q].items()}
    return msg


ENCODERS = {"json": encode_json, "cbor": encode_cbor}
DECODERS = {"json": json.loads, "cbor": cbor2.loads}
