"""Формат → байт/сообщение → трафик/сутки.

Генерирует N сообщений той же моделью, что и устройство, и кодирует каждое четырьмя способами.
    python tools/size_compare.py --n 2000 --out results/size_compare.json
"""

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "simulator"))
from codec import (decode_cbor_compact, encode_cbor, encode_cbor_compact,  # noqa: E402
                   encode_json, encode_json_pretty)
from device import Device  # noqa: E402

FORMATS = {
    "JSON (с пробелами)": (encode_json_pretty, "json"),
    "JSON компактный": (encode_json, "json"),
    "CBOR": (encode_cbor, "cbor"),
    "CBOR компактный": (encode_cbor_compact, "cbor"),
}
TCPIP = 2 * 52 + 4        # заголовки TCP/IP пакета PUBLISH и ответного PUBACK + сам PUBACK


def publish_size(topic: str, payload: int) -> int:
    remaining = 2 + len(topic) + 2 + payload          # длина топика, топик, packet id, payload
    return 1 + (1 if remaining < 128 else 2) + remaining


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--out")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    random.seed(1)

    dev = Device("room-sensor-01", interval=args.interval)
    t0 = 1792281600.0                                  # 2026-10-18 00:00 UTC
    msgs = [dev.sample(t0 + i * args.interval) for i in range(args.n)]

    # компактный CBOR теряет только масштабированную точность — проверяем обратимость
    for m in msgs:
        d = decode_cbor_compact(encode_cbor_compact(m))
        assert d["seq"] == m["seq"] and d["metrics"]["motion"] == m["metrics"]["motion"]
        for c in ("temperature", "humidity", "pressure"):
            a, b = m["metrics"][c], d["metrics"][c]
            assert (a is None and b is None) or abs(a - b) < 1e-9, (c, a, b)

    per_day = 86400 / args.interval
    base = None
    rows = []
    for name, (enc, suffix) in FORMATS.items():
        sizes = [len(enc(m)) for m in msgs]
        mean = statistics.mean(sizes)
        topic = f"enu/site1/room-sensor-01/up/telemetry/{suffix}"
        pkt = statistics.mean(publish_size(topic, s) for s in sizes)
        base = base or None
        rows.append({"format": name, "payload_mean": mean, "payload_min": min(sizes),
                     "payload_max": max(sizes), "mqtt_packet": pkt, "wire": pkt + TCPIP,
                     "mb_day": mean * per_day / 1e6, "wire_mb_day": (pkt + TCPIP) * per_day / 1e6})
    ref = rows[1]                                       # опорный — компактный JSON
    for r in rows:
        r["saving_payload_pct"] = 100 * (1 - r["payload_mean"] / ref["payload_mean"])
        r["saving_wire_pct"] = 100 * (1 - r["wire"] / ref["wire"])
        r["fleet1000_gb_day"] = r["mb_day"]             # 1000 устройств × МБ = ГБ
        r["fleet10000_gb_month"] = r["mb_day"] * 10 * 30

    print(f"Сообщений: {args.n}, интервал {args.interval:g} с → {per_day:.0f} сообщений/сутки")
    print(f"{'Формат':<20}{'payload, Б (min–max)':>24}{'MQTT, Б':>9}{'экономия':>10}"
          f"{'на линии':>10}{'МБ/сутки':>10}{'10k устр., ГБ/мес':>19}")
    for r in rows:
        print(f"{r['format']:<20}{r['payload_mean']:>9.1f} ({r['payload_min']}–{r['payload_max']})"
              f"{'':>4}{r['mqtt_packet']:>9.1f}{r['saving_payload_pct']:>9.1f}%"
              f"{r['saving_wire_pct']:>9.1f}%{r['mb_day']:>10.2f}{r['fleet10000_gb_month']:>19.1f}")
    print("Компактный CBOR: обратимость проверена на всех сообщениях (seq, motion, значения каналов)")
    ex = msgs[0]
    print("Пример сообщения:", encode_json(ex).decode())
    print(f"  JSON {len(encode_json(ex))} Б | CBOR {len(encode_cbor(ex))} Б | "
          f"CBOR компактный {len(encode_cbor_compact(ex))} Б: {encode_cbor_compact(ex).hex()}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"n": args.n, "interval_s": args.interval, "rows": rows,
                       "example": ex}, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
