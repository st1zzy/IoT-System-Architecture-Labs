"""Байты на проводе по каждому обмену QoS 0/1/2 из захвата (fields.csv от tshark).

Обмен = все кадры (в обе стороны, включая TCP ACK) от PUBLISH до следующего PUBLISH.
"""

import json
import sys
from pathlib import Path

NAMES = {1: "CONNECT", 2: "CONNACK", 3: "PUBLISH", 4: "PUBACK", 5: "PUBREC", 6: "PUBREL", 7: "PUBCOMP",
         12: "PINGREQ", 13: "PINGRESP", 14: "DISCONNECT"}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    out = Path(sys.argv[1])
    rows = []
    for line in (out / "fields.csv").read_text().splitlines():
        num, t, flen, src, tcplen, mtype, qos, mlen = (line.split(";") + [""] * 8)[:8]
        rows.append({"n": int(num), "t": float(t), "len": int(flen), "src": src, "tcp": int(tcplen or 0),
                     "type": [int(x) for x in mtype.split(",") if x], "qos": qos})
    demo = (out / "demo.txt").read_text(encoding="utf-8")
    payload = int(demo.split("(")[-1].split(" ")[0])
    topic_len = int(demo.split("(")[1].split(" ")[0])
    pubs = [r for r in rows if 3 in r["type"]]
    conn = [r for r in rows if r["t"] < pubs[0]["t"]]
    print(f"Подключение (TCP handshake + CONNECT/CONNACK): {len(conn)} кадров, {sum(r['len'] for r in conn)} Б")
    res = []
    for i, p in enumerate(pubs):
        t_end = pubs[i + 1]["t"] if i + 1 < len(pubs) else p["t"] + 0.9
        ex = [r for r in rows if p["t"] <= r["t"] < min(t_end, p["t"] + 0.9)]
        types = [NAMES.get(x, str(x)) for r in ex for x in r["type"]]
        total = sum(r["len"] for r in ex)
        mqtt_pub = p["tcp"]
        res.append({"qos": int(p["qos"]), "frames": len(ex), "bytes": total, "publish_frame": p["len"],
                    "mqtt_publish": mqtt_pub, "payload": payload, "packets": types,
                    "efficiency_pct": 100 * payload / total})
        print(f"QoS {p['qos']}: {len(ex)} кадров, {total} Б на проводе; кадр PUBLISH {p['len']} Б = "
              f"Ethernet 14 + IP 20 + TCP {p['len'] - 34 - mqtt_pub} + MQTT {mqtt_pub} "
              f"(заголовок {mqtt_pub - payload - topic_len - (2 if int(p['qos']) else 0) - 2}, длина топика 2, "
              f"топик {topic_len}{', packet id 2' if int(p['qos']) else ''}, payload {payload}); "
              f"полезная нагрузка {100 * payload / total:.1f} % ({' '.join(types)})")
    disc = [r for r in rows if 14 in r["type"]]
    print(f"Payload {payload} Б, топик {topic_len} Б; кадров всего {len(rows)}, DISCONNECT {len(disc)}")
    (out / "wire.json").write_text(json.dumps({"connect_bytes": sum(r["len"] for r in conn),
                                               "connect_frames": len(conn), "exchanges": res,
                                               "payload": payload, "topic_len": topic_len},
                                              indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
