"""Все эксперименты Lab 7 (запуск с хоста, стенд поднят docker compose up -d).

    python tools/run_experiments.py [имена…]   # по умолчанию все: deadband main limited_raw unlimited sendmode crash overflow
Сырые данные → results/<эксперимент>.json, журнал → stdout.
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
GW, ING = "http://localhost:8081/stats", "http://localhost:8082"


def sh(*cmd: str, env: dict | None = None, check: bool = True) -> str:
    r = subprocess.run(list(cmd), capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
                       env={**os.environ, **(env or {})})
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr.strip()}")
    return r.stdout.strip()


def get(url: str) -> dict:
    for _ in range(30):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return json.loads(r.read())
        except OSError:
            time.sleep(1)
    raise RuntimeError(f"нет ответа {url}")


def post(url: str) -> None:
    urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=10).read()


def say(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def gateway(**env) -> None:
    """Пересоздать шлюз с параметрами (буфер на томе сохраняется — дожидаемся, пока он опустеет)."""
    e = {k: str(v) for k, v in env.items()}
    sh("docker", "compose", "up", "-d", "--force-recreate", "gateway", env=e)
    time.sleep(3)
    get(GW)
    wan("up")
    wait_drained(300)


def wan(state: str) -> str:
    return sh("docker", "exec", "lab7-gateway", "sh", "/usr/local/bin/wan", state, check=False)


def overheat(pump: int) -> float:
    t = time.time()
    sh("docker", "exec", "lab7-modbus", "python", "-c",
       "from pymodbus.client import ModbusTcpClient as C; c=C('localhost', port=5020); c.connect(); "
       f"c.write_coil({pump - 1}, True, slave=1); c.close()")
    return t


def wait_drained(timeout: float, upto: int | None = None) -> float:
    """Ждать, пока в буфере не останется записей с seq ≤ upto (или пока буфер не станет «живым»: < BATCH)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = get(GW)
        if s["connected"] and (s["low_seq"] is None or (upto is not None and s["low_seq"] > upto)
                               or (upto is None and s["buffer"] < 40)):
            return time.time() - t0
        time.sleep(1)
    raise RuntimeError("буфер не опустел")


class Sampler:
    """Раз в секунду: состояние шлюза; раз в ~2 с: CPU брокера north и ingest (docker stats)."""

    def __init__(self):
        self.gw, self.cpu, self.stop = [], [], threading.Event()
        threading.Thread(target=self._gw, daemon=True).start()
        threading.Thread(target=self._cpu, daemon=True).start()

    def _gw(self):
        while not self.stop.is_set():
            try:
                s = get(GW)
                self.gw.append({k: s[k] for k in ("t", "buffer", "connected", "sent_records", "acked_records",
                                                  "enqueued", "samples", "dropped", "last_seq")})
            except RuntimeError:
                pass
            self.stop.wait(1)

    def _cpu(self):
        while not self.stop.is_set():
            out = sh("docker", "stats", "--no-stream", "--format", "{{.Name}};{{.CPUPerc}};{{.MemUsage}}",
                     "lab7-north", "lab7-ingest", "lab7-db", check=False)
            t = time.time()
            for line in out.splitlines():
                n, c, m = line.split(";")
                self.cpu.append({"t": t, "name": n, "cpu": float(c.rstrip("%") or 0), "mem": m.split(" /")[0]})

    def done(self):
        self.stop.set()
        return {"gw": self.gw, "cpu": self.cpu}


def analyze(seq_from: int, seq_to: int, dropped0: int = 0) -> dict:
    """Потери/дубли по seq в интервале (seq_from, seq_to] по данным ingest; dropped0 — счётчик вытеснений на старте."""
    d = get(f"{ING}/dump")
    arr = [a for a in d["arrivals"] if seq_from < a[0] <= seq_to]
    got = [a[0] for a in arr]
    uniq = set(got)
    s = get(GW)
    return {"expected": seq_to - seq_from, "received_unique": len(uniq), "dups": len(got) - len(uniq),
            "missing": seq_to - seq_from - len(uniq), "dropped_by_gateway": s["dropped"] - dropped0,
            "missing_seqs": sorted(set(range(seq_from + 1, seq_to + 1)) - uniq)[:200], "arrivals": arr}


def cycle(label: str, online_s: float, outage_s: float, inject_at: float | None, settle_s: float = 30) -> dict:
    """online → wan down (outage_s) → wan up → ресинхронизация. Возвращает хронологию."""
    ev = {"label": label}
    time.sleep(online_s)
    ev["seq_before"] = get(GW)["last_seq"]
    ev["down"] = time.time()
    say(f"{label}: {wan('down')}")
    if inject_at is not None:
        time.sleep(inject_at)
        ev["overheat"] = overheat(1)
        say(f"{label}: перегрев насоса 1 (во время обрыва)")
        time.sleep(outage_s - inject_at)
    else:
        time.sleep(outage_s)
    st = get(GW)
    ev["buffer_at_up"], ev["seq_at_up"] = st["buffer"], st["last_seq"]
    ev["up"] = time.time()
    say(f"{label}: {wan('up')}, в буфере {st['buffer']} записей")
    ev["resync_s"] = wait_drained(900, upto=ev["seq_at_up"])
    ev["resynced"] = time.time()
    say(f"{label}: ресинхронизация {ev['resync_s']:.0f} с")
    time.sleep(settle_s)
    return ev


def exp_deadband() -> dict:
    """Сырой поток без фильтра 6 мин (с одним перегревом) → raw.csv для офлайн-анализа deadband."""
    gateway(DB_MODE="off", RAW_LOG=1)
    sh("docker", "exec", "lab7-gateway", "sh", "-c", ": > /data/raw.csv")
    s0 = get(GW)
    time.sleep(150)
    t_oh = overheat(2)
    time.sleep(210)
    s1 = get(GW)
    sh("docker", "cp", "lab7-gateway:/data/raw.csv", str(RES / "raw_trace.csv"))
    say(f"deadband: сырой поток {s1['samples'] - s0['samples']} отсчётов за 360 с")
    return {"samples": s1["samples"] - s0["samples"], "overheat": t_oh}


def exp_main() -> dict:
    """3 цикла «обрыв 5 мин → восстановление», ограничение backfill 200 записей/с, приоритет алармов."""
    gateway(DB_MODE="abs", BACKFILL_RATE=200)
    post(f"{ING}/reset")
    seq0, drop0 = get(GW)["last_seq"], get(GW)["dropped"]
    smp = Sampler()
    online_alarm = None
    cycles = []
    for i in range(1, 4):
        if i == 1:
            time.sleep(40)
            online_alarm = overheat(2)                 # аларм при наличии связи — для сравнения задержек
            say("main: перегрев насоса 2 (связь есть)")
            cycles.append(cycle("main-1", 80, 300, 120))
        else:
            cycles.append(cycle(f"main-{i}", 120, 300, 120))
    seq_end = get(GW)["last_seq"]
    wait_drained(300, upto=seq_end)
    time.sleep(5)
    return {"seq_from": seq0, "seq_to": seq_end, "online_alarm": online_alarm, "cycles": cycles,
            **smp.done(), **analyze(seq0, seq_end, drop0), "ingest": get(f"{ING}/stats")}


def exp_single(name: str, outage_s: float, **env) -> dict:
    gateway(**{"DB_MODE": "abs", **env})
    post(f"{ING}/reset")
    seq0, drop0 = get(GW)["last_seq"], get(GW)["dropped"]
    smp = Sampler()
    cycles = [cycle(f"{name}-{i}", 60, outage_s, 60) for i in range(1, 4 if env.get("ACK_MODE") == "send" else 2)]
    seq_end = get(GW)["last_seq"]
    wait_drained(300, upto=seq_end)
    time.sleep(5)
    return {"seq_from": seq0, "seq_to": seq_end, "env": env, "cycles": cycles, **smp.done(),
            **analyze(seq0, seq_end, drop0), "ingest": get(f"{ING}/stats")}


def exp_crash() -> dict:
    """Обрыв WAN, через 120 с — kill -9 шлюза («пропало питание»), старт через 10 с: буфер должен уцелеть."""
    gateway(DB_MODE="abs", BACKFILL_RATE=200)
    post(f"{ING}/reset")
    seq0, drop0 = get(GW)["last_seq"], get(GW)["dropped"]
    smp = Sampler()
    time.sleep(60)
    ev = {"down": time.time()}
    say(f"crash: {wan('down')}")
    time.sleep(120)
    st = get(GW)
    ev.update(buffer_before_kill=st["buffer"], seq_before_kill=st["last_seq"], kill=time.time())
    sh("docker", "kill", "--signal", "KILL", "lab7-gateway")
    say(f"crash: kill -9 шлюза, в буфере было {st['buffer']}")
    time.sleep(10)
    sh("docker", "start", "lab7-gateway")
    ev["start"] = time.time()
    time.sleep(3)
    st = get(GW)
    ev["buffer_after_start"] = st["buffer"]
    say(f"crash: шлюз запущен, в буфере {st['buffer']} (WAN после рестарта контейнера восстановлен)")
    ev["resync_s"] = wait_drained(600, upto=ev["seq_before_kill"])
    seq_end = get(GW)["last_seq"]
    wait_drained(300, upto=seq_end)
    time.sleep(5)
    log = sh("docker", "logs", "--since", "4m", "lab7-gateway", check=False)
    return {"seq_from": seq0, "seq_to": seq_end, "events": ev, **smp.done(), **analyze(seq0, seq_end, drop0),
            "gateway_log": [ln for ln in log.splitlines() if "буфер" in ln or "north" in ln][-12:]}


def exp_overflow() -> dict:
    """Буфер 1000 записей, обрыв 5 мин: вытеснение старых обычных записей, аларм (старше их) сохраняется."""
    gateway(DB_MODE="abs", BACKFILL_RATE=200, BUFFER_MAX=1000)
    post(f"{ING}/reset")
    seq0, drop0 = get(GW)["last_seq"], get(GW)["dropped"]
    smp = Sampler()
    cycles = [cycle("overflow", 30, 300, 20)]
    seq_end = get(GW)["last_seq"]
    wait_drained(300, upto=seq_end)
    time.sleep(5)
    return {"seq_from": seq0, "seq_to": seq_end, "cycles": cycles, **smp.done(), **analyze(seq0, seq_end, drop0)}


PLAN = {
    "deadband": exp_deadband,
    "main": exp_main,
    # без deadband (40 записей/с) накопление за 4 мин ≈ 9600 записей — близко к ёмкости буфера
    "limited_raw": lambda: exp_single("limited_raw", 240, BACKFILL_RATE=200, DB_MODE="off"),
    "unlimited": lambda: exp_single("unlimited", 240, BACKFILL_RATE=0, DB_MODE="off"),
    "sendmode": lambda: exp_single("sendmode", 300, BACKFILL_RATE=200, ACK_MODE="send"),
    "crash": exp_crash,
    "overflow": exp_overflow,
}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    RES.mkdir(exist_ok=True)
    for name in sys.argv[1:] or list(PLAN):
        say(f"=== {name}")
        res = PLAN[name]()
        (RES / f"{name}.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        keys = ("expected", "received_unique", "missing", "dups", "dropped_by_gateway")
        say(f"{name}: " + ", ".join(f"{k}={res[k]}" for k in keys if k in res))
    gateway()                                         # штатная конфигурация
    say("готово")


if __name__ == "__main__":
    main()
