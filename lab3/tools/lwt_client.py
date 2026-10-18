"""Клиент с LWT для эксперимента: online (retained) при подключении, offline — завещание."""

import argparse
import time

import paho.mqtt.client as mqtt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--keepalive", type=int, required=True)
    args = ap.parse_args()
    topic = f"lab3/lwt/{args.id}/status"
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.id)
    c.will_set(topic, "offline", qos=1, retain=True)
    c.connect("lab3-mosquitto", 1883, keepalive=args.keepalive)
    c.loop_start()
    c.publish(topic, "online", qos=1, retain=True)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
