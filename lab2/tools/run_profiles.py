"""Серии измерений канала (запускается внутри lab2-client).

Для каждого профиля: tc netem в обе стороны → ping → iperf3 → MQTT QoS 0/1/2 → время подключения.
    docker exec lab2-client python tools/run_profiles.py --series 3
Сырые результаты: results/raw/s{N}/{profile}.json
"""

import argparse
import json
import re
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).parent))
from channel_model import PROFILES, Profile  # noqa: E402

BROKER, IPERF = "lab2-mosquitto", "lab2-iperf"
OUT = Path("/lab/results/raw")
PAYLOAD = 190                 # размер сообщения Lab 1 (компактный JSON)
V2 = mqtt.CallbackAPIVersion.VERSION2


def sh(cmd: str, check: bool = True) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{cmd}: {r.stderr.strip()}")
    return r.stdout


def apply_netem(p: Profile | None) -> None:
    """netem на исходящем трафике eth0 и на входящем через ifb0 — симметричный канал."""
    sh("tc qdisc del dev eth0 root", check=False)
    sh("tc qdisc del dev eth0 ingress", check=False)
    sh("ip link del ifb0", check=False)
    if p is None:
        return
    spec = f"netem limit 200 {p.netem()}"
    sh(f"tc qdisc add dev eth0 root {spec}")
    sh("ip link add ifb0 type ifb && ip link set ifb0 up")
    sh("tc qdisc add dev eth0 handle ffff: ingress")
    sh("tc filter add dev eth0 parent ffff: protocol all u32 match u32 0 0 "
       "action mirred egress redirect dev ifb0")
    sh(f"tc qdisc add dev ifb0 root {spec}")


def run_ping(n: int = 200) -> dict:
    out = sh(f"ping -c {n} -i 0.05 -W 2 {BROKER}", check=False)
    rtts = [float(x) for x in re.findall(r"time=([\d.]+) ms", out)]
    sent = int(re.search(r"(\d+) packets transmitted", out).group(1))
    return {"sent": sent, "received": len(rtts), "loss_pct": 100 * (1 - len(rtts) / sent),
            "rtt_ms": rtts}


def run_iperf(seconds: int = 5) -> dict:
    out = sh(f"iperf3 -c {IPERF} -t {seconds} -J", check=False)
    try:
        j = json.loads(out)
        return {"mbps": j["end"]["sum_received"]["bits_per_second"] / 1e6,
                "sent_mbps": j["end"]["sum_sent"]["bits_per_second"] / 1e6,
                "retransmits": j["end"]["sum_sent"].get("retransmits", 0)}
    except (ValueError, KeyError):
        return {"mbps": 0.0, "sent_mbps": 0.0, "retransmits": None, "error": out[-300:]}


def run_mqtt(qos: int, n: int = 1000, rate: float = 100.0, drain_s: float = 30.0) -> dict:
    """n сообщений с частотой rate: издатель → брокер → подписчик (канал проходится дважды)."""
    topic = f"lab2/test/q{qos}/{time.time_ns()}"
    got: dict[int, float] = {}
    dups = 0
    lock = threading.Lock()

    def on_msg(_c, _u, m):
        nonlocal dups
        t = time.time()
        seq, ts = struct.unpack_from("!Id", m.payload)
        with lock:
            if seq in got:
                dups += 1
            else:
                got[seq] = (t - ts) * 1000

    sub = mqtt.Client(V2, client_id=f"sub-{topic[-8:]}")
    sub.on_message = on_msg
    ready = threading.Event()
    sub.on_subscribe = lambda *_: ready.set()
    sub.connect(BROKER, 1883, keepalive=60)
    sub.loop_start()
    sub.subscribe(topic, qos=qos)
    ready.wait(15)
    pub = mqtt.Client(V2, client_id=f"pub-{topic[-8:]}")
    pub.max_inflight_messages_set(20)
    pub.max_queued_messages_set(0)
    pub.connect(BROKER, 1883, keepalive=60)
    pub.loop_start()
    t0 = time.time()
    for seq in range(n):
        target = t0 + seq / rate
        time.sleep(max(0.0, target - time.time()))
        pub.publish(topic, struct.pack("!Id", seq, time.time()).ljust(PAYLOAD, b"x"), qos=qos)
    sent_s = time.time() - t0
    deadline = time.time() + drain_s
    while time.time() < deadline:
        with lock:
            k = len(got)
        if k >= n:
            break
        time.sleep(0.2)
    pub.loop_stop(); pub.disconnect()
    sub.loop_stop(); sub.disconnect()
    with lock:
        lat = [got[s] for s in sorted(got)]
    return {"qos": qos, "sent": n, "delivered": len(lat), "dups": dups, "publish_s": sent_s,
            "latency_ms": lat}


def mqtt_connect_packet(client_id: str) -> bytes:
    var = b"\x00\x04MQTT\x04\x02\x00\x3c"                    # MQTT 3.1.1, clean session, keepalive 60
    payload = struct.pack("!H", len(client_id)) + client_id.encode()
    rem = len(var) + len(payload)
    return bytes([0x10, rem]) + var + payload


def run_connect(variant: str, n: int = 30, timeout: float = 10.0) -> dict:
    """Фазы подключения: TCP 3-way handshake → TLS → MQTT CONNECT/CONNACK.

    Клиент ставит TCP_NODELAY, как MQTT-библиотеки. Задержку после TLS 1.3 вызывает сторона
    брокера: см. tools/nagle_compare.sh и set_tcp_nodelay в mosquitto.conf.
    """
    ctx = None
    if variant != "plain":
        ctx = ssl.create_default_context(cafile="/certs/server.crt")
        ver = ssl.TLSVersion.TLSv1_2 if variant == "tls12" else ssl.TLSVersion.TLSv1_3
        ctx.minimum_version = ctx.maximum_version = ver
    nodelay = True
    port = 1883 if variant == "plain" else 8883
    samples, fails = [], 0
    for i in range(n):
        try:
            t0 = time.perf_counter()
            s = socket.create_connection((BROKER, port), timeout=timeout)
            t1 = time.perf_counter()
            if nodelay:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if ctx:
                s = ctx.wrap_socket(s, server_hostname=BROKER)
            t2 = time.perf_counter()
            s.sendall(mqtt_connect_packet(f"conn-{variant}-{i}"))
            ack = s.recv(4)
            t3 = time.perf_counter()
            if ack[:1] != b"\x20" or ack[3:4] != b"\x00":
                raise RuntimeError(f"bad CONNACK {ack!r}")
            s.sendall(b"\xe0\x00")                             # DISCONNECT
            s.close()
            samples.append({"tcp": (t1 - t0) * 1e3, "tls": (t2 - t1) * 1e3, "mqtt": (t3 - t2) * 1e3})
        except (OSError, RuntimeError, ssl.SSLError):
            fails += 1
    return {"variant": variant, "n": n, "fails": fails, "samples": samples}


def pct(v: list[float], p: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, int(round((len(s) - 1) * p / 100)))] if s else float("nan")


def connect_only(label: str, profiles: list[Profile], n: int) -> None:
    """Только замер подключения TLS 1.2/1.3 — для сравнения настроек брокера."""
    for p in profiles:
        apply_netem(p)
        time.sleep(1)
        res = {"label": label, "profile": p.name, "rssi": p.rssi,
               "connect": [run_connect(v, n=n) for v in ("tls12", "tls13")]}
        d = OUT / "nagle"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{label}_{p.name}.json").write_text(json.dumps(res), encoding="utf-8")
        for c in res["connect"]:
            ph = {k: pct([x[k] for x in c["samples"]], 50) for k in ("tcp", "tls", "mqtt")}
            print(f"{label:<8} {p.name} {c['variant']}: TCP {ph['tcp']:.1f} + TLS {ph['tls']:.1f} + "
                  f"MQTT {ph['mqtt']:.1f} мс (p50, n={len(c['samples'])}, отказов {c['fails']})",
                  flush=True)
    apply_netem(None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--connect-only", metavar="LABEL")
    ap.add_argument("--apply", metavar="PROFILE", help="только включить профиль netem")
    ap.add_argument("--clear", action="store_true", help="снять netem")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--series", type=int, default=3)
    ap.add_argument("--profiles", default=",".join(p.name for p in PROFILES))
    args = ap.parse_args()
    chosen = [p for p in PROFILES if p.name in args.profiles.split(",")]
    sys.stdout.reconfigure(encoding="utf-8")
    if args.clear or args.apply:
        p = next((x for x in PROFILES if x.name == args.apply), None)
        apply_netem(p)
        print(f"netem: {p.name} {p.netem()}" if p else "netem: off")
        return
    if args.connect_only:
        connect_only(args.connect_only, chosen, args.n)
        return
    for s in range(1, args.series + 1):
        for p in chosen:
            apply_netem(p)
            time.sleep(1)
            t = time.time()
            res = {"series": s, "profile": p.name, "distance_m": p.distance_m, "rssi": p.rssi,
                   "mcs": p.mcs, "rate_mbps": p.rate_mbps, "netem": p.netem(),
                   "ping": run_ping(), "iperf": run_iperf(),
                   "mqtt": [run_mqtt(q) for q in (0, 1, 2)],
                   "connect": [run_connect(v) for v in ("plain", "tls12", "tls13")]}
            res["elapsed_s"] = time.time() - t
            d = OUT / f"s{s}"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{p.name}.json").write_text(json.dumps(res), encoding="utf-8")
            pg, ip = res["ping"], res["iperf"]
            mq = " ".join(f"q{m['qos']}:{m['delivered']}/{m['sent']} p99={pct(m['latency_ms'], 99):.0f}ms"
                          for m in res["mqtt"])
            cn = " ".join(f"{c['variant']}={pct([x['tcp'] + x['tls'] + x['mqtt'] for x in c['samples']], 50):.0f}ms"
                          for c in res["connect"])
            print(f"s{s} {p.name} RSSI={p.rssi:.1f} | ping loss={pg['loss_pct']:.1f}% "
                  f"p50={pct(pg['rtt_ms'], 50):.1f}ms | iperf={ip['mbps']:.2f}Mbit/s "
                  f"retr={ip['retransmits']} | {mq} | connect p50 {cn} | {res['elapsed_s']:.0f}s",
                  flush=True)
    apply_netem(None)


if __name__ == "__main__":
    main()
