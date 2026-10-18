"""Subscriber: подписки с wildcard + и #, подсчёт сообщений и детекция пропусков по seq.

Каждый фильтр — отдельный клиент, чтобы пересекающиеся подписки не смешивались в подсчёте.
    python tools/subscriber.py --filter 'enu/+/+/up/telemetry/temperature' --filter 'enu/#' --duration 65
"""

import argparse
import json
import sys
import threading
import time
from collections import Counter, defaultdict

import paho.mqtt.client as mqtt

V2 = mqtt.CallbackAPIVersion.VERSION2
LOCK = threading.Lock()          # клиенты работают в своих потоках — печать по одной строке


def say(text: str) -> None:
    with LOCK:
        print(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="lab3-mosquitto")
    ap.add_argument("--filter", action="append", required=True)
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--show", type=int, default=3, help="напечатать первые N сообщений каждого фильтра")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    stats = {f: {"n": 0, "topics": Counter(), "last": {}, "gaps": [], "dups": 0, "retained": 0,
                 "shown": 0} for f in args.filter}
    clients = []
    for i, flt in enumerate(args.filter):
        st = stats[flt]

        def on_msg(_c, _u, m, st=st, flt=flt):
            st["n"] += 1
            st["topics"][m.topic] += 1
            if m.retain:
                st["retained"] += 1
            if st["shown"] < args.show:
                st["shown"] += 1
                say(f"[{flt}] {m.topic} qos={m.qos} retain={int(m.retain)} {m.payload.decode()[:80]}")
            try:
                seq = json.loads(m.payload)["seq"]
            except (ValueError, KeyError, TypeError):
                return                                       # status и config — без seq
            prev = st["last"].get(m.topic)
            if prev is not None:
                if seq == prev:
                    st["dups"] += 1
                elif seq > prev + 1:
                    st["gaps"].append((m.topic, prev + 1, seq - 1))
                    say(f"[{flt}] ПРОПУСК {m.topic}: seq {prev + 1}..{seq - 1} ({seq - prev - 1} шт.)")
            st["last"][m.topic] = max(seq, prev or 0)

        c = mqtt.Client(V2, client_id=f"sub-{i}")
        c.on_message = on_msg
        c.on_connect = lambda cl, *_, flt=flt: cl.subscribe(flt, qos=1)
        c.connect(args.host, 1883)
        c.loop_start()
        clients.append(c)
    time.sleep(args.duration)
    for c in clients:
        c.loop_stop()
        c.disconnect()
    for flt, st in stats.items():
        missing = sum(b - a + 1 for _, a, b in st["gaps"])
        print(f"ИТОГ [{flt}]: сообщений {st['n']}, топиков {len(st['topics'])}, retained {st['retained']}, "
              f"пропусков {len(st['gaps'])} (сообщений {missing}), дублей {st['dups']}")
    groups = defaultdict(int)
    for t, n in stats[args.filter[-1]]["topics"].items():
        groups["/".join(t.split("/")[3:5]) if len(t.split("/")) > 4 else t.split("/")[-1]] += n
    print("  по классам (последний фильтр): " + ", ".join(f"{k} {v}" for k, v in sorted(groups.items())))


if __name__ == "__main__":
    main()
