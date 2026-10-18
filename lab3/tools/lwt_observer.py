"""Наблюдатель presence на стороне backend: каждое изменение статуса — строка JSON с временем."""

import argparse
import json
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=600)
    ap.add_argument("--out", default="/lab/results/lwt/events.jsonl")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    f = out.open("a", encoding="utf-8")

    def on_msg(_c, _u, m):
        ev = {"t": time.time(), "client": m.topic.split("/")[2], "status": m.payload.decode(),
              "retain": bool(m.retain)}
        f.write(json.dumps(ev) + "\n")
        f.flush()
        print(f"{time.strftime('%H:%M:%S')} {m.topic} = {ev['status']} retain={int(m.retain)}")

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="lwt-observer")
    c.on_message = on_msg
    c.on_connect = lambda cl, *_: cl.subscribe("lab3/lwt/+/status", qos=1)
    c.connect("lab3-mosquitto", 1883)
    c.loop_start()
    time.sleep(args.duration)
    c.loop_stop()


if __name__ == "__main__":
    main()
