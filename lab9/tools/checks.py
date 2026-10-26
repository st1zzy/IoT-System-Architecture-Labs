"""Проверки Lab 9 внутри lab9-client. Каждая печатает ожидание/факт и пишет results/<имя>.json.

    python tools/checks.py pki | tls | capture | cost | acl | probe | hold
"""

import json
import os
import ssl
import statistics as st
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sec import BROKER, CHAIN, ROGUE_CA, WEAK, connect_wait, mqtt5, raw_connect, tls_ctx  # noqa: E402

import paho.mqtt.client as mqtt  # noqa: E402

OUT = Path("/lab/results")
V2 = mqtt.CallbackAPIVersion.VERSION2
RESULT: list[dict] = []


def check(name: str, expect: str, fact: str, ok: bool, **extra) -> None:
    RESULT.append({"check": name, "expected": expect, "actual": fact, "pass": ok, **extra})
    print(f"  [{'OK' if ok else 'FAIL'}] {name}: ожидалось «{expect}» → {fact}", flush=True)


def sh(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def save(name: str, extra: dict | None = None) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps({"checks": RESULT, **(extra or {})}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")


# ---------------------------------------------------------------------------------------------------- 1. PKI
def c_pki() -> None:
    print("1. Иерархия PKI")
    for n in ("broker", "dev-001", "dev-002", "dev-003", "svc-ingest"):
        out = sh(f"openssl verify -CAfile /pki/root/root.crt -untrusted /pki/int/int.crt /pki/certs/{n}.crt")
        check(f"цепочка {n}", "OK", out.split(": ")[-1], out.endswith("OK"))
    out = sh("openssl verify -CAfile /pki/root/root.crt -untrusted /pki/int/int.crt /pki/certs/rogue.crt 2>&1")
    check("цепочка rogue (CN=dev-001, чужой CA)", "ошибка", out.splitlines()[-1][:80], "OK" not in out.splitlines()[-1])
    for n, f in (("Root CA", "/pki/root/root.crt"), ("Intermediate CA", "/pki/int/int.crt")):
        bc = sh(f"openssl x509 -in {f} -noout -ext basicConstraints | tail -1").strip()
        check(f"{n}: basicConstraints", "CA:TRUE с pathlen", bc, "CA:TRUE" in bc and "pathlen" in bc)
    info = sh("openssl x509 -in /pki/certs/dev-001.crt -noout -subject -issuer -dates -ext extendedKeyUsage")
    print("     " + info.replace("\n", "\n     "))
    save("pki", {"dev001": info, "chain": sh("openssl crl2pkcs7 -nocrl -certfile /pki/chain.pem | "
                                             "openssl pkcs7 -print_certs -noout")})


# --------------------------------------------------------------------------------------------- 2–3. TLS/mTLS
def c_tls() -> None:
    print("2–3. TLS 1.3 и взаимная аутентификация")
    cases = [
        ("mTLS dev-001, TLS 1.3", "подключено, TLS 1.3", dict(port=8883, ctx=tls_ctx("dev-001"), client_id="dev-001"), True),
        ("клиент TLS 1.2 на :8883", "отказ: protocol version", dict(port=8883, ctx=tls_ctx("dev-001", "1.2"), client_id="dev-001"), False),
        ("без клиентского сертификата", "отказ: certificate required", dict(port=8883, ctx=tls_ctx(None), client_id="anon"), False),
        ("сертификат чужого CA с CN=dev-001", "отказ: unknown ca", dict(port=8883, ctx=tls_ctx("rogue"), client_id="dev-001"), False),
        ("просроченный сертификат", "отказ: certificate expired", dict(port=8883, ctx=tls_ctx("dev-expired"), client_id="x"), False),
        ("клиент: неверное имя брокера", "отказ клиента: hostname mismatch",
         dict(port=8883, ctx=tls_ctx("dev-001"), client_id="dev-001", server_hostname="evil.example"), False),
        ("клиент: MITM с сертификатом чужого CA", "отказ клиента: verify failed",
         dict(port=8883, ctx=tls_ctx("dev-001", ca=ROGUE_CA), client_id="dev-001"), False),
    ]
    for name, expect, kw, should in cases:
        r = raw_connect(BROKER, **kw)
        fact = f"подключено, {r['version']}, {r['cipher']}" if r["ok"] else f"отказ на фазе {r['phase']}: {r.get('error', '')[:90]}"
        check(name, expect, fact, r["ok"] == should)
    c = mqtt5("dev-001", "dev-001")
    rc = connect_wait(c, BROKER, 8883)
    check("use_identity_as_username: имя из CN", "CONNACK Success", rc, rc == "Success")
    c.disconnect()
    save("tls")


# ---------------------------------------------------------------------------------------- 4. перехват трафика
def c_capture() -> None:
    print("4. Перехват трафика до и после TLS")
    pcap = "/lab/results/capture.pcap"
    td = subprocess.Popen(["tcpdump", "-i", "any", "-w", pcap, "-U", "tcp port 1883 or tcp port 8883"],
                          stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    secret = '{"dev":"dev-001","temperature":21.53,"secret":"VALVE-KEY-4711"}'
    for host, port, tls in ((WEAK, 1883, False), (BROKER, 8883, True)):
        c = mqtt5("dev-001" if tls else None, "dev-001", tls=tls)
        connect_wait(c, host, port)
        c.publish("site/dev-001/telemetry", secret, qos=1).wait_for_publish(3)
        time.sleep(0.5)
        c.disconnect()
        c.loop_stop()
    time.sleep(1.5)
    td.terminate()
    td.wait()
    raw = open(pcap, "rb").read()
    plain = sh(f"tshark -r {pcap} -Y 'tcp.port==1883 && mqtt' -T fields -e mqtt.msgtype -e mqtt.topic -e mqtt.msg 2>/dev/null")
    enc = sh(f"tshark -r {pcap} -Y 'tcp.port==8883 && tls' -T fields -e tls.record.content_type -e tls.handshake.type "
             f"-e tls.record.length 2>/dev/null")
    found_plain = b"VALVE-KEY-4711" in raw and b"site/dev-001/telemetry" in raw
    n_plain = raw.count(b"VALVE-KEY-4711")
    tls_frames = sh(f"tshark -r {pcap} -Y 'tcp.port==8883' -T fields -e frame.len 2>/dev/null").split()
    check("секрет в открытом трафике :1883", "читается (topic и payload)", f"найден в pcap {n_plain} раз(а)", found_plain)
    sni = sh(f"tshark -r {pcap} -Y 'tls.handshake.type==1' -T fields -e tls.handshake.extensions_server_name 2>/dev/null")
    sh(f"tshark -r {pcap} -Y 'tcp.port==8883' -w /tmp/tls_only.pcap 2>/dev/null")
    tls_raw = open("/tmp/tls_only.pcap", "rb").read()
    check("секрет в TLS-трафике :8883", "не читается", "не найден" if b"VALVE-KEY-4711" not in tls_raw else "НАЙДЕН",
          b"VALVE-KEY-4711" not in tls_raw and b"site/dev-001" not in tls_raw)
    (OUT / "console").mkdir(exist_ok=True)
    (OUT / "console" / "capture_plain.txt").write_text(sh(f"tshark -r {pcap} -Y 'tcp.port==1883 && mqtt' 2>/dev/null"), encoding="utf-8")
    (OUT / "console" / "capture_tls.txt").write_text(sh(f"tshark -r {pcap} -Y 'tcp.port==8883 && tls' 2>/dev/null"), encoding="utf-8")
    (OUT / "console" / "capture_plain_hex.txt").write_text(
        sh(f"tshark -r {pcap} -Y 'tcp.port==1883 && mqtt.msgtype==3' -x 2>/dev/null | head -14"), encoding="utf-8")
    (OUT / "console" / "capture_tls_hex.txt").write_text(
        sh(f"tshark -r {pcap} -Y 'tcp.port==8883 && tls.record.content_type==23' -x 2>/dev/null | sed -n '1,14p'"), encoding="utf-8")
    save("capture", {"plain_mqtt": plain, "tls_records": enc, "sni": sni, "tls_frames": len(tls_frames)})


# ------------------------------------------------------------------------------------- 4б. стоимость TLS
def peer_ip(host: str) -> str:
    return sh(f"getent hosts {host} | awk '{{print $1}}'")


def netem(delay_ms: int) -> None:
    sh("tc qdisc del dev eth0 root 2>/dev/null")
    if delay_ms:
        sh("tc qdisc add dev eth0 root handle 1: prio && "
           f"tc qdisc add dev eth0 parent 1:3 handle 30: netem delay {delay_ms}ms && "
           f"tc filter add dev eth0 parent 1:0 protocol ip u32 match ip dst {peer_ip(BROKER)}/32 flowid 1:3 && "
           f"tc filter add dev eth0 parent 1:0 protocol ip u32 match ip dst {peer_ip(WEAK)}/32 flowid 1:3")


def stream_bytes(pcap: str, sports: list[int]) -> list[int]:
    rows = sh(f"tshark -r {pcap} -T fields -e tcp.srcport -e tcp.dstport -e frame.len 2>/dev/null").splitlines()
    tot = {p: 0 for p in sports}
    for r in rows:
        a, b, n = r.split("\t")
        for p in (int(a), int(b)):
            if p in tot:
                tot[p] += int(n)
    return [tot[p] for p in sports]


def c_cost() -> None:
    print("4б. Стоимость TLS: время подключения и байты (30 подключений на вариант)")
    N = 30
    variants = [("plain", WEAK, 1883, None), ("TLS 1.2 mTLS", BROKER, 8884, "1.2"),
                ("TLS 1.3 mTLS", BROKER, 8883, "1.3"), ("TLS 1.3 mTLS + resumption", BROKER, 8883, "resume")]
    res = {}
    for delay in (0, 40):
        netem(delay)
        for name, host, port, ver in variants:
            pcap = f"/tmp/cost_{port}_{delay}.pcap"
            td = subprocess.Popen(["tcpdump", "-i", "eth0", "-w", pcap, "-U", f"tcp port {port}"], stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            ctx = tls_ctx("dev-001", "1.3" if ver == "resume" else ver) if ver else None
            sess, rows = None, []
            if ver == "resume":                                        # получить билет сессии
                first = raw_connect(host, port, ctx, "dev-001", keep=True)
                first["sock"].settimeout(0.5)
                try:
                    first["sock"].recv(1)
                except (TimeoutError, OSError, ssl.SSLError):
                    pass
                sess = first["sock"].session
                first["sock"].close()
            for _ in range(N):
                r = raw_connect(host, port, ctx, "dev-001", session=sess)
                if ver == "resume" and r.get("session") is not None:
                    sess = r["session"]
                rows.append(r)
                time.sleep(0.05)
            time.sleep(1.0)
            td.terminate()
            td.wait()
            byts = stream_bytes(pcap, [r["sport"] for r in rows if "sport" in r])
            ok = [r for r in rows if r["ok"]]
            key = f"{name} @RTT+{delay}"
            res[key] = {"variant": name, "delay_ms": delay, "n": N, "ok": len(ok),
                        "total_ms": st.median([r["total_ms"] for r in ok]) if ok else None,
                        "tcp_ms": st.median([r["tcp_ms"] for r in ok]) if ok else None,
                        "tls_ms": st.median([r["tls_ms"] for r in ok]) if ok else None,
                        "mqtt_ms": st.median([r["mqtt_ms"] for r in ok]) if ok else None,
                        "p95_ms": sorted(r["total_ms"] for r in ok)[int(0.95 * (len(ok) - 1))] if ok else None,
                        "bytes": st.median(byts) if byts else None,
                        "reused": sum(1 for r in ok if r.get("reused"))}
            x = res[key]
            print(f"  {key:<34} успешно {x['ok']}/{N}, медиана {x['total_ms']:.1f} мс (TCP {x['tcp_ms']:.1f} + TLS "
                  f"{x['tls_ms']:.1f} + MQTT {x['mqtt_ms']:.1f}), p95 {x['p95_ms']:.1f} мс, {x['bytes']:.0f} Б на соединение"
                  + (f", возобновлено {x['reused']}/{N}" if ver == "resume" else ""), flush=True)
    netem(0)
    # накладные расходы на сообщение: 200 публикаций по 64 Б в одном соединении
    per_msg = {}
    for name, host, port, tls in (("plain", WEAK, 1883, False), ("TLS 1.3", BROKER, 8883, True)):
        pcap = f"/tmp/msg_{port}.pcap"
        td = subprocess.Popen(["tcpdump", "-i", "eth0", "-w", pcap, "-U", f"tcp port {port}"], stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        c = mqtt5("dev-001" if tls else None, "dev-001", host=host, port=port, tls=tls)
        connect_wait(c, host, port)
        time.sleep(0.5)
        mark = time.time()
        for i in range(200):
            c.publish("site/dev-001/telemetry", ("x" * 64), qos=0)
            time.sleep(0.01)
        time.sleep(1.0)
        c.disconnect()
        c.loop_stop()
        time.sleep(0.5)
        td.terminate()
        td.wait()
        rows = sh(f"tshark -r {pcap} -T fields -e frame.time_epoch -e tcp.srcport -e frame.len 2>/dev/null").splitlines()
        b = sum(int(r.split("\t")[2]) for r in rows if float(r.split("\t")[0]) >= mark and r.split("\t")[1] != str(port))
        per_msg[name] = b / 200
        print(f"  {name}: {b / 200:.1f} Б на сообщение 64 Б (клиент → брокер, с заголовками TCP/IP)")
    save("cost", {"connect": res, "per_message": per_msg})


# ---------------------------------------------------------------------------------------------- 5. ACL по CN
def c_acl() -> None:
    print("5. ACL по CN сертификата и попытка выдать себя за другое устройство")
    got: list[tuple[str, str]] = []
    ing = mqtt5("svc-ingest", "svc-ingest")
    ing.on_message = lambda _c, _u, m: got.append((m.topic, m.payload.decode()))
    connect_wait(ing, BROKER, 8883)
    ing.subscribe("site/+/telemetry", qos=1)
    weak_got: list[tuple[str, str]] = []
    wmon = mqtt.Client(V2, client_id="weak-ingest", protocol=mqtt.MQTTv5)
    wmon.on_message = lambda _c, _u, m: weak_got.append((m.topic, m.payload.decode()))
    connect_wait(wmon, WEAK, 1883)
    wmon.subscribe("site/+/telemetry", qos=1)
    time.sleep(1)

    def pub(c, topic, payload) -> str:
        rc = {}
        ev = threading.Event()
        c.on_publish = lambda _c, _u, mid, reason, _p: (rc.setdefault("rc", str(reason)), ev.set())
        c.publish(topic, payload, qos=1)
        ev.wait(3)
        time.sleep(0.5)
        return rc.get("rc", "нет PUBACK")

    d1 = mqtt5("dev-001", "dev-001")
    connect_wait(d1, BROKER, 8883)
    rc = pub(d1, "site/dev-001/telemetry", "own-001")
    check("dev-001 → свой топик", "PUBACK Success, доставлено", f"PUBACK {rc}, доставлено: {('site/dev-001/telemetry', 'own-001') in got}",
          rc == "Success" and ("site/dev-001/telemetry", "own-001") in got)
    rc = pub(d1, "site/dev-002/telemetry", "fake-from-001")
    check("dev-001 → топик dev-002", "отказ Not authorized", f"PUBACK {rc}, доставлено: {any(p == 'fake-from-001' for _, p in got)}",
          rc == "Not authorized" and not any(p == "fake-from-001" for _, p in got))
    d1.disconnect()
    d1.loop_stop()
    spoof = mqtt5("dev-001", "dev-002")                     # сертификат dev-001, client_id dev-002
    rc0 = connect_wait(spoof, BROKER, 8883)
    rc = pub(spoof, "site/dev-002/telemetry", "spoof-as-002")
    check("подмена client_id: cert dev-001, client_id dev-002", "отказ — имя берётся из сертификата",
          f"CONNACK {rc0}, PUBACK {rc}, доставлено: {any(p == 'spoof-as-002' for _, p in got)}",
          rc == "Not authorized" and not any(p == "spoof-as-002" for _, p in got))
    spoof.disconnect()
    spoof.loop_stop()
    # подписка на чужие команды: SUBACK выдаётся, но доставка запрещена
    stolen = []
    snoop = mqtt5("dev-001", "dev-001-snoop")
    snoop.on_message = lambda _c, _u, m: stolen.append(m.topic)
    connect_wait(snoop, BROKER, 8883)
    snoop.subscribe("site/dev-002/cmd", qos=1)
    time.sleep(0.5)
    ing.publish("site/dev-002/cmd", '{"cmd":"open"}', qos=1)
    time.sleep(1)
    check("dev-001 читает команды dev-002", "не доставляется", f"получено {len(stolen)}", not stolen)
    snoop.disconnect()
    snoop.loop_stop()
    # слабый брокер: ACL по client_id — подмена удаётся
    w = mqtt.Client(V2, client_id="dev-002", protocol=mqtt.MQTTv5)
    connect_wait(w, WEAK, 1883)
    rc = pub(w, "site/dev-002/telemetry", "attacker-as-002")
    check("слабый брокер: анонимный клиент с client_id dev-002", "подмена удаётся (антипаттерн)",
          f"PUBACK {rc}, доставлено: {any(p == 'attacker-as-002' for _, p in weak_got)}",
          any(p == "attacker-as-002" for _, p in weak_got))
    w.disconnect()
    save("acl")


# ------------------------------------------------------------------------------------------ 6. отзыв (CRL)
def c_probe() -> None:
    """Попытки подключения dev-003 каждые 0,25 с в течение N с: время каждой и исход."""
    dur = float(sys.argv[2]) if len(sys.argv) > 2 else 20
    out = []
    t0 = time.time()
    while time.time() - t0 < dur:
        t = time.time()
        r = raw_connect(BROKER, 8883, tls_ctx("dev-003"), "dev-003-probe")
        out.append({"t": t, "ok": r["ok"], "error": r.get("error", "")[:80]})
        time.sleep(0.25)
    Path("/lab/results/probe.jsonl").open("a").write("".join(json.dumps(x) + "\n" for x in out))


def c_hold() -> None:
    """Сеанс dev-003, установленный ДО отзыва: публикация раз в 0,5 с, журнал PUBACK и разрывов."""
    dur = float(sys.argv[2]) if len(sys.argv) > 2 else 90
    log = Path("/lab/results/hold.jsonl").open("w", buffering=1)
    c = mqtt5("dev-003", "dev-003")
    c.reconnect_delay_set(1, 2)
    c.on_publish = lambda _c, _u, mid, rc, _p: log.write(json.dumps({"t": time.time(), "ev": "puback", "rc": str(rc)}) + "\n")
    c.on_disconnect = lambda _c, _u, _f, rc, _p: log.write(json.dumps({"t": time.time(), "ev": "disconnect", "rc": str(rc)}) + "\n")
    print("hold: CONNACK", connect_wait(c, BROKER, 8883), flush=True)
    c.on_connect = lambda _c, _u, _f, rc, _p: log.write(json.dumps({"t": time.time(), "ev": "connect", "rc": str(rc)}) + "\n")
    t0 = time.time()
    k = 0
    while time.time() - t0 < dur:
        c.publish("site/dev-003/telemetry", json.dumps({"seq": k}), qos=1)
        k += 1
        time.sleep(0.5)
    c.loop_stop()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    {"pki": c_pki, "tls": c_tls, "capture": c_capture, "cost": c_cost, "acl": c_acl,
     "probe": c_probe, "hold": c_hold}[sys.argv[1]]()
