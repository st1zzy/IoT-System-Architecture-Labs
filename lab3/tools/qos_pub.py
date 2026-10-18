"""Сторона устройства в эксперименте QoS: n сообщений с заданным QoS через канал с потерями.

Сессия persistent (clean_session=False), keep-alive короткий: при потерях соединение рвётся,
и paho после переподключения повторно отправляет неподтверждённые QoS 1/2 (флаг DUP),
а QoS 0, отправленные при разрыве, теряются.
    python tools/qos_pub.py --run q1_l20_s1 --qos 1 --loss 20
"""

import argparse
import json
import struct
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).parent))
import netem  # noqa: E402

V2 = mqtt.CallbackAPIVersion.VERSION2
OUT = Path("/lab/results/qos")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--qos", type=int, required=True)
    ap.add_argument("--loss", type=float, required=True)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--rate", type=float, default=50)
    ap.add_argument("--keepalive", type=int, default=5)
    ap.add_argument("--drain", type=float, default=60)
    ap.add_argument("--inflight", type=int, default=50, help="окно неподтверждённых сообщений клиента")
    ap.add_argument("--v5", action="store_true", help="MQTT 5: клиент соблюдает Receive Maximum брокера")
    ap.add_argument("--outage", type=float, default=0,
                    help="два провала связи (100 %% потерь) этой длительности, с — на 1/3 и 2/3 отправки")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    events = {"connects": 0, "disconnects": 0, "publish_err": 0}
    if args.v5:
        c = mqtt.Client(V2, client_id=f"pub-{args.run}", protocol=mqtt.MQTTv5)
    else:
        c = mqtt.Client(V2, client_id=f"pub-{args.run}", clean_session=False)
    c.max_inflight_messages_set(args.inflight)
    c.reconnect_delay_set(min_delay=1, max_delay=4)
    c.on_connect = lambda *_: events.__setitem__("connects", events["connects"] + 1)
    c.on_disconnect = lambda *_: events.__setitem__("disconnects", events["disconnects"] + 1)
    acked_mids: set[int] = set()       # подтверждения брокера (PUBACK / PUBCOMP) — в т.ч. для досланных после разрыва
    failed: dict[str, int] = {}        # MQTT 5: подтверждения с кодом ошибки (в 3.1.1 кодов причины нет)

    def on_publish(_c, _u, mid, reason_code, _props):
        acked_mids.add(mid)
        if getattr(reason_code, "is_failure", False):
            failed[str(reason_code)] = failed.get(str(reason_code), 0) + 1

    c.on_publish = on_publish
    if args.v5:
        from paho.mqtt.packettypes import PacketTypes
        from paho.mqtt.properties import Properties
        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = 3600                 # аналог clean_session=False
        c.connect("lab3-mosquitto", 1883, keepalive=args.keepalive, clean_start=False, properties=props)
    else:
        c.connect("lab3-mosquitto", 1883, keepalive=args.keepalive)
    c.loop_start()
    time.sleep(1)
    netem.apply(args.loss)
    infos = []
    t0 = time.time()
    try:
        for seq in range(args.n):
            time.sleep(max(0.0, t0 + seq / args.rate - time.time()))
            if args.outage and seq in (args.n // 3, 2 * args.n // 3):
                netem.apply(100)                       # соединение рвётся по keep-alive
                time.sleep(args.outage)
                netem.apply(args.loss)
                t0 += args.outage                      # отправка продолжается по расписанию
            payload = struct.pack("!Id", seq, time.time()).ljust(64, b"\0")
            info = c.publish(f"lab3/qos/{args.run}", payload, qos=args.qos)
            if info.rc != mqtt.MQTT_ERR_SUCCESS and args.qos == 0:
                events["publish_err"] += 1               # QoS 0 при отсутствии соединения — отброшено
            # QoS 1/2 при разрыве paho держит в очереди и отправит после переподключения
            infos.append(info)
        send_s = time.time() - t0
        deadline = time.time() + args.drain
        # QoS 1/2, отправленные при разрыве, paho держит в очереди; is_published() для них бросает исключение,
        # поэтому ждём подтверждений по mid
        expected = {i.mid for i in infos if args.qos > 0 or i.rc == mqtt.MQTT_ERR_SUCCESS}
        while time.time() < deadline and not expected <= acked_mids:
            time.sleep(0.2)
    finally:
        netem.clear()
    time.sleep(2)
    acked = len({i.mid for i in infos} & acked_mids)
    c.loop_stop()
    c.disconnect()
    res = {"run": args.run, "qos": args.qos, "loss": args.loss, "n": args.n, "send_s": send_s,
           "inflight": args.inflight, "outage": args.outage, "v5": args.v5, "failed_acks": failed,
           "acked": acked, **events}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{args.run}_pub.json").write_text(json.dumps(res), encoding="utf-8")
    print(f"pub {args.run}: QoS {args.qos}, потери {args.loss:g} %, отправлено {args.n} за {send_s:.1f} с, "
          f"подтверждено брокером {acked}, разрывов {events['disconnects']}, отброшено при разрыве "
          f"{events['publish_err']}" + (f", подтверждений с ошибкой: {failed}" if failed else ""))


if __name__ == "__main__":
    main()
