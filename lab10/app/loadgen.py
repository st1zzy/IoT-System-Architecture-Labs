"""Генератор нагрузки Lab 10: N виртуальных устройств, у каждого своё TCP/MQTT-соединение.

Минимальный клиент MQTT 3.1.1 на asyncio (CONNECT / PUBLISH QoS 1 / PUBACK / DISCONNECT) —
paho на поток-на-клиента не держит 20 000 соединений в одном контейнере.
Клиенты делятся между процессами (по ~2 500 на процесс), процесс = uvloop.

Политики переподключения (--policy):
  immediate   — сразу, затем каждую 1 с (типичный «while(!connect()) delay(1000)»)
  exp         — b, 2b, 4b, … с (cap 60 с) — экспоненциальная задержка без джиттера (b = --backoff-base)
  exp_jitter  — U(0, b·2ⁿ) с (cap 60 с) — «full jitter»
--buffer: сообщения, сгенерированные без связи, и неподтверждённые (inflight) не теряются, а
          отправляются после переподключения (до 600 на устройство).
"""
import argparse
import asyncio
import json
import multiprocessing as mp
import os
import random
import struct
import sys
import time
from collections import deque

MAX_INFLIGHT = 32
BUF_MAX = 600
BIN = 0.1                      # шаг временных рядов, с


def varint(n):
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def mstr(b: bytes):
    return struct.pack("!H", len(b)) + b


def connect_pkt(cid: str, keepalive: int):
    body = mstr(b"MQTT") + b"\x04\x02" + struct.pack("!H", keepalive) + mstr(cid.encode())
    return b"\x10" + varint(len(body)) + body


def publish_pkt(topic: bytes, pid: int, payload: bytes):
    body = mstr(topic) + struct.pack("!H", pid) + payload
    return b"\x32" + varint(len(body)) + body


async def read_packet(reader):
    h = (await reader.readexactly(1))[0]
    mult, n = 1, 0
    while True:
        b = (await reader.readexactly(1))[0]
        n += (b & 0x7F) * mult
        mult *= 128
        if not b & 0x80:
            break
    body = await reader.readexactly(n) if n else b""
    return h, body


class Stats:
    def __init__(self):
        self.c = dict(generated=0, sent=0, acked=0, dropped_offline=0, dropped_buffer=0, dropped_window=0,
                      lost_inflight=0, resent=0, conn_attempts=0, conn_ok=0, conn_fail=0,
                      disconnects=0, reconnects=0, devices_disconnected=0)
        self.attempts = {}      # bin -> попыток CONNECT
        self.fails = {}         # bin -> неудачных попыток
        self.acks = {}          # bin -> PUBACK
        self.connected = {}     # bin -> подключено сейчас (снимок)
        self.connack_ms = []    # задержка CONNECT → CONNACK, мс
        self.reconnect_s = []   # время от разрыва до восстановления, с
        self.now_connected = 0

    def add(self, d, t0):
        b = int((time.time() - t0) / BIN)
        d[b] = d.get(b, 0) + 1


def backoff(policy, attempt, base=1.0):
    if policy == "immediate":
        return 0.0 if attempt == 0 else 1.0
    cap = min(60.0, base * 2 ** attempt)
    return cap if policy == "exp" else random.uniform(0, cap)


class Device:
    __slots__ = ("cid", "topic", "w", "inflight", "buf", "pid", "seq", "a", "st", "t0", "down_at", "was_down", "up")

    def __init__(self, idx, a, st, t0):
        self.cid = f"{a.run}-{idx:05d}"
        self.topic = f"telemetry/{self.cid}".encode()
        self.w = None
        self.inflight = {}
        self.buf = deque()
        self.pid = 0
        self.seq = 0
        self.a, self.st, self.t0 = a, st, t0
        self.down_at = None
        self.was_down = False
        self.up = asyncio.Event()          # первое подключение: публикация начинается после него

    def payload(self):
        ms = int(time.time() * 1000)
        p = (f'{{"r":"{self.a.run}","d":"{self.cid}","s":{self.seq},"t":{ms},'
             f'"temp":{random.uniform(18, 26):.2f},"hum":{random.uniform(30, 60):.1f},'
             f'"bat":{random.uniform(3.4, 4.2):.3f}}}').encode()
        self.seq += 1
        return p

    def transmit(self, p):
        self.pid = self.pid % 65535 + 1
        self.inflight[self.pid] = p
        self.w.write(publish_pkt(self.topic, self.pid, p))
        self.st.c["sent"] += 1

    def offer(self, p):
        """Новое сообщение: отправить, отложить в буфер или потерять."""
        self.st.c["generated"] += 1
        if self.a.buffer:
            if len(self.buf) < BUF_MAX:
                self.buf.append(p)
            else:
                self.st.c["dropped_buffer"] += 1
            self.flush()
        elif self.w is None:
            self.st.c["dropped_offline"] += 1
        elif len(self.inflight) < MAX_INFLIGHT:
            self.transmit(p)
        else:
            self.st.c["dropped_window"] += 1          # окно inflight переполнено

    def flush(self):
        while self.buf and self.w is not None and len(self.inflight) < MAX_INFLIGHT:
            self.transmit(self.buf.popleft())


async def session(dev: Device, end_t: float):
    """Подключение + чтение; возвращается при разрыве соединения или по окончании."""
    a, st = dev.a, dev.st
    attempt = 0
    while time.time() < end_t + a.drain:
        st.c["conn_attempts"] += 1
        st.add(st.attempts, dev.t0)
        t_conn = time.time()
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(a.host, a.port), a.timeout)
            writer.write(connect_pkt(dev.cid, a.keepalive))
            h, body = await asyncio.wait_for(read_packet(reader), a.timeout - (time.time() - t_conn))
            if h >> 4 != 2 or body[1] != 0:
                raise ConnectionError(f"CONNACK rc={body[1]}")
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, IndexError):
            st.c["conn_fail"] += 1
            st.add(st.fails, dev.t0)
            try:
                writer.close()
            except Exception:
                pass
            await asyncio.sleep(backoff(a.policy, attempt, a.backoff_base))
            attempt += 1
            continue
        st.connack_ms.append((time.time() - t_conn) * 1000)
        st.c["conn_ok"] += 1
        dev.up.set()
        st.now_connected += 1
        if dev.down_at is not None:
            st.c["reconnects"] += 1
            st.reconnect_s.append(time.time() - dev.down_at)
            dev.down_at = None
        attempt = 0
        dev.w = writer
        if a.buffer and dev.inflight:           # неподтверждённые — повторно (новые packet id)
            old = list(dev.inflight.values())
            dev.inflight.clear()
            st.c["resent"] += len(old)
            dev.buf.extendleft(reversed(old))
        dev.flush()
        try:
            while True:
                h, body = await read_packet(reader)
                if h >> 4 == 4:                 # PUBACK
                    if dev.inflight.pop(struct.unpack("!H", body[:2])[0], None) is not None:
                        st.c["acked"] += 1
                        st.add(st.acks, dev.t0)
                        if dev.buf:
                            dev.flush()
        except (OSError, asyncio.IncompleteReadError):
            pass
        # разрыв
        dev.w = None
        st.now_connected -= 1
        st.c["disconnects"] += 1
        if not dev.was_down:
            dev.was_down = True
            st.c["devices_disconnected"] += 1
        dev.down_at = time.time()
        if not a.buffer:
            st.c["lost_inflight"] += len(dev.inflight)
            dev.inflight.clear()
        try:
            writer.close()
        except Exception:
            pass
        await asyncio.sleep(backoff(a.policy, 0, a.backoff_base))
        attempt = 1


async def device_main(dev: Device, idx: int, a, t0: float, start_pub: float, end_t: float):
    await asyncio.sleep(max(0.0, t0 + idx / a.conn_rate - time.time()))
    sess = asyncio.create_task(session(dev, end_t))
    try:
        await asyncio.wait_for(dev.up.wait(), max(0.0, end_t - time.time()))
    except asyncio.TimeoutError:
        pass
    period = 1.0 / a.rate
    nxt = max(time.time(), start_pub) + random.random() * period
    while nxt < end_t:
        await asyncio.sleep(max(0.0, nxt - time.time()))
        dev.offer(dev.payload())
        nxt += period
    # отправка закончена: ждём подтверждений, затем штатный DISCONNECT
    deadline = time.time() + a.drain
    while (dev.inflight or dev.buf) and time.time() < deadline:
        await asyncio.sleep(0.1)
    sess.cancel()
    if dev.w is not None:
        try:
            dev.w.write(b"\xe0\x00")  # DISCONNECT — штатное завершение
            dev.w.close()
        except Exception:
            pass


async def proc_main(ids, a, t0, start_pub, end_t, out):
    st = Stats()
    devs = [Device(i, a, st, t0) for i in ids]

    async def sampler():
        while True:
            st.connected[int((time.time() - t0) / BIN)] = st.now_connected
            await asyncio.sleep(BIN)

    smp = asyncio.create_task(sampler())
    await asyncio.gather(*(device_main(d, i, a, t0, start_pub, end_t) for d, i in zip(devs, ids)))
    smp.cancel()
    st.c["inflight_left"] = sum(len(d.inflight) for d in devs)
    st.c["buffer_left"] = sum(len(d.buf) for d in devs)
    out.put(dict(c=st.c, attempts=st.attempts, fails=st.fails, acks=st.acks, connected=st.connected,
                 connack_ms=st.connack_ms, reconnect_s=st.reconnect_s,
                 per_device={d.cid: d.seq for d in devs}))


def worker(ids, a, t0, start_pub, end_t, out):
    import uvloop
    uvloop.run(proc_main(ids, a, t0, start_pub, end_t, out))


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--clients", type=int, default=1000)
    ap.add_argument("--rate", type=float, default=1.0, help="сообщений/с на устройство")
    ap.add_argument("--duration", type=float, default=60, help="длительность публикации после разгона, с")
    ap.add_argument("--conn-rate", type=float, default=1000, help="подключений/с при разгоне")
    ap.add_argument("--host", default="nginx")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--policy", default="exp_jitter", choices=["immediate", "exp", "exp_jitter"])
    ap.add_argument("--backoff-base", type=float, default=1.0, help="первая задержка (окно джиттера), с")
    ap.add_argument("--buffer", action="store_true")
    ap.add_argument("--timeout", type=float, default=5.0, help="таймаут TCP+CONNACK, с")
    ap.add_argument("--keepalive", type=int, default=60)
    ap.add_argument("--drain", type=float, default=10.0)
    ap.add_argument("--per-proc", type=int, default=2500)
    ap.add_argument("--t0", type=float, default=0, help="общий момент старта (unix time), 0 = через 2 с")
    a = ap.parse_args()

    t0 = a.t0 or time.time() + 2
    ramp = a.clients / a.conn_rate
    start_pub = t0 + ramp
    end_t = start_pub + a.duration
    nproc = max(1, -(-a.clients // a.per_proc))
    out = mp.Queue()
    procs = []
    for k in range(nproc):
        ids = list(range(k, a.clients, nproc))
        p = mp.Process(target=worker, args=(ids, a, t0, start_pub, end_t, out))
        p.start()
        procs.append(p)
    parts = [out.get() for _ in procs]
    for p in procs:
        p.join()

    c = {k: sum(p["c"][k] for p in parts) for k in parts[0]["c"]}

    def merge(key):
        m = {}
        for p in parts:
            for b, v in p[key].items():
                m[b] = m.get(b, 0) + v
        return m

    conn = {}
    last_bin = max(max(p["connected"]) for p in parts if p["connected"])
    for p in parts:                      # «подключено» — сумма снимков процессов (пропуски — последним значением)
        v = 0
        for b in range(last_bin + 1):
            v = p["connected"].get(b, v)
            conn[b] = conn.get(b, 0) + v
    connack = [x for p in parts for x in p["connack_ms"]]
    recon = [x for p in parts for x in p["reconnect_s"]]
    attempts = merge("attempts")
    res = dict(
        run=a.run, args=vars(a), t0=t0, start_pub=start_pub, end_t=end_t, nproc=nproc,
        counters=c,
        connack_ms=dict(n=len(connack), p50=pct(connack, 50), p95=pct(connack, 95), p99=pct(connack, 99),
                        max=max(connack) if connack else None),
        reconnect_s=dict(n=len(recon), p50=pct(recon, 50), p95=pct(recon, 95), p99=pct(recon, 99),
                         max=max(recon) if recon else None),
        peak_attempts_per_bin=max(attempts.values()) if attempts else 0,
        series=dict(bin=BIN, attempts=attempts, fails=merge("fails"), acks=merge("acks"), connected=conn),
    )
    os.makedirs("/lab/results/runs", exist_ok=True)
    with open(f"/lab/results/runs/{a.run}_loadgen.json", "w") as f:
        json.dump(res, f)
    gen = c["generated"]
    print(f"[{a.run}] clients={a.clients} rate={a.rate}/s procs={nproc} policy={a.policy} buffer={a.buffer}")
    print(f"  connect: attempts={c['conn_attempts']} ok={c['conn_ok']} fail={c['conn_fail']} "
          f"connack p50/p99={res['connack_ms']['p50'] and round(res['connack_ms']['p50'], 1)}/"
          f"{res['connack_ms']['p99'] and round(res['connack_ms']['p99'], 1)} ms")
    print(f"  messages: generated={gen} sent={c['sent']} acked={c['acked']} "
          f"dropped_offline={c['dropped_offline']} lost_inflight={c['lost_inflight']} "
          f"inflight_left={c['inflight_left']} buffer_left={c['buffer_left']}")
    print(f"  disconnects={c['disconnects']} devices_disconnected={c['devices_disconnected']} "
          f"reconnects={c['reconnects']}")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
