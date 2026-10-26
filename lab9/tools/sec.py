"""Общие функции Lab 9: TLS-контекст, MQTT-подключение по сырому сокету с замером фаз, MQTT 5 клиенты."""

import socket
import ssl
import struct
import time

import paho.mqtt.client as mqtt

BROKER, WEAK = "lab9-broker", "lab9-weak"
CHAIN, ROGUE_CA = "/pki/chain.pem", "/pki/rogue-ca.crt"
V2 = mqtt.CallbackAPIVersion.VERSION2


def cert(name: str) -> tuple[str, str]:
    return f"/pki/certs/{name}.crt", f"/pki/certs/{name}.key"


def tls_ctx(name: str | None, version: str = "1.3", ca: str = CHAIN) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(ca)
    v = ssl.TLSVersion.TLSv1_3 if version == "1.3" else ssl.TLSVersion.TLSv1_2
    ctx.minimum_version = ctx.maximum_version = v
    if name:
        ctx.load_cert_chain(*cert(name))
    return ctx


def connect_packet(client_id: str) -> bytes:
    var = b"\x00\x04MQTT\x04\x02\x00\x3c"                      # MQTT 3.1.1, clean session, keep-alive 60
    payload = struct.pack("!H", len(client_id)) + client_id.encode()
    return bytes([0x10, len(var) + len(payload)]) + var + payload


def raw_connect(host: str, port: int, ctx: ssl.SSLContext | None, client_id: str,
                server_hostname: str = BROKER, session=None, keep: bool = False) -> dict:
    """TCP → (TLS) → CONNECT/CONNACK. Возвращает фазы (мс), версию, шифр, исход и ошибку на какой фазе."""
    r = {"ok": False, "phase": "tcp"}
    t0 = time.perf_counter()
    try:
        s = socket.create_connection((host, port), timeout=10)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        t1 = time.perf_counter()
        r["tcp_ms"] = (t1 - t0) * 1000
        r["sport"] = s.getsockname()[1]
        if ctx:
            r["phase"] = "tls"
            s = ctx.wrap_socket(s, server_hostname=server_hostname, session=session)
            r["version"], r["cipher"] = s.version(), s.cipher()[0]
        t2 = time.perf_counter()
        r["tls_ms"] = (t2 - t1) * 1000
        r["phase"] = "mqtt"
        s.sendall(connect_packet(client_id))
        ack = s.recv(4)
        t3 = time.perf_counter()
        if len(ack) < 4:
            raise ConnectionError("соединение закрыто без CONNACK")
        r["mqtt_ms"] = (t3 - t2) * 1000
        r["total_ms"] = (t3 - t0) * 1000
        r["connack_rc"] = ack[3]
        r["ok"] = ack[0] == 0x20 and ack[3] == 0
        if ctx:
            r["session"] = s.session
            r["reused"] = s.session_reused
        if keep:
            r["sock"] = s
        else:
            s.sendall(b"\xe0\x00")
            s.close()
    except (OSError, ssl.SSLError) as e:
        r["error"] = f"{type(e).__name__}: {e}"
    return r


def mqtt5(name: str | None, client_id: str, host: str = BROKER, port: int = 8883, tls: bool = True) -> mqtt.Client:
    c = mqtt.Client(V2, client_id=client_id, protocol=mqtt.MQTTv5)
    if tls:
        c.tls_set_context(tls_ctx(name))
    return c


def connect_wait(c: mqtt.Client, host: str, port: int, timeout: float = 5) -> str:
    import threading
    ev, res = threading.Event(), {}

    def on_connect(_c, _u, _f, rc, _p):
        res["rc"] = str(rc)
        ev.set()
    c.on_connect = on_connect
    try:
        c.connect(host, port, keepalive=30)
    except (OSError, ssl.SSLError) as e:
        return f"{type(e).__name__}: {e}"
    c.loop_start()
    ev.wait(timeout)
    return res.get("rc", "нет CONNACK")
