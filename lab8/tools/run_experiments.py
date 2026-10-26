"""Прогоны Lab 8 (с хоста, стенд поднят). Каждый прогон — 600 с, 200 устройств, 60 аномалий, свой seed.

edge-1: режим edge, WAN без задержки, 30 проб перегрева (задержка локальной реакции)
edge-2: режим edge, WAN 40±10 мс в каждую сторону, 30 проб
edge-3: режим edge, обрыв WAN 3 мин (240–420 с), 20 проб во время обрыва — управление без облака
raw-1:  режим raw (всё сырое в облако, правило и детектор в облаке): 30 проб без задержки,
        30 проб при WAN 40±10 мс, 10 проб во время обрыва 60 с
    python tools/run_experiments.py [edge-1 edge-2 edge-3 raw-1]
"""

import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
SIM, EDGE, CLOUD = "http://localhost:8090", "http://localhost:8091/stats", "http://localhost:8092/"
DURATION = 600


def sh(*cmd: str, env: dict | None = None) -> str:
    r = subprocess.run(list(cmd), capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
                       env={**os.environ, **(env or {})})
    return r.stdout.strip()


def get(url: str):
    for _ in range(60):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except OSError:
            time.sleep(1)
    raise RuntimeError(url)


def post(url: str) -> None:
    for _ in range(30):
        try:
            urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=10).read()
            return
        except OSError:
            time.sleep(1)


def say(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def wan(*args: str) -> str:
    return sh("docker", "exec", "lab8-edge", "sh", "/usr/local/bin/wan", *args)


def at(t0: float, t: float) -> None:
    time.sleep(max(0.0, t0 + t - time.time()))


def trials(t0: float, start: float, n: int, rng: random.Random, gap: float = 3.0) -> None:
    for i in range(n):
        at(t0, start + i * gap)
        post(f"{SIM}/overheat?dev={rng.randrange(200)}")


class CpuSampler:
    def __init__(self):
        self.rows, self.stop = [], threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.stop.is_set():
            out = sh("docker", "stats", "--no-stream", "--format", "{{.Name}};{{.CPUPerc}};{{.MemUsage}}",
                     "lab8-edge", "lab8-cloud-svc", "lab8-cloud")
            for line in out.splitlines():
                n, c, m = line.split(";")
                self.rows.append({"t": time.time(), "name": n, "cpu": float(c.rstrip("%") or 0), "mem": m.split(" /")[0]})


def run(name: str, seed: int, mode: str) -> dict:
    say(f"=== {name}: seed {seed}, режим {mode}")
    sh("docker", "compose", "up", "-d", "--force-recreate", "sim", "edge",
       env={"SEED": str(seed), "EDGE_MODE": mode, "DURATION": str(DURATION)})
    post(CLOUD)
    truth0 = get(f"{SIM}/truth")
    t0 = truth0["t0"]
    post(f"{SIM}/reset")
    e0 = get(EDGE)
    wan("up")
    cpu = CpuSampler()
    rng = random.Random(seed)
    phases = []
    if name == "edge-1":
        trials(t0, 130, 30, rng)
        phases.append({"label": "edge, WAN 0 мс", "from": 130, "to": 220})
    elif name == "edge-2":
        at(t0, 100)
        say(wan("delay", "40", "10"))
        trials(t0, 130, 30, rng)
        phases.append({"label": "edge, WAN 40±10 мс", "from": 130, "to": 220})
    elif name == "edge-3":
        at(t0, 240)
        say(wan("down"))
        trials(t0, 270, 20, rng, gap=6)
        phases.append({"label": "edge, WAN оборван", "from": 270, "to": 390})
        at(t0, 420)
        say(wan("up"))
    elif name == "raw-1":
        trials(t0, 130, 30, rng)
        phases.append({"label": "cloud, WAN 0 мс", "from": 130, "to": 220})
        at(t0, 240)
        say(wan("delay", "40", "10"))
        trials(t0, 250, 30, rng)
        phases.append({"label": "cloud, WAN 40±10 мс", "from": 250, "to": 340})
        at(t0, 360)
        say(wan("up"))
        at(t0, 400)
        say(wan("down"))
        trials(t0, 410, 10, rng, gap=4)
        phases.append({"label": "cloud, WAN оборван", "from": 410, "to": 450})
        at(t0, 460)
        say(wan("up"))
    at(t0, DURATION + 75)                   # последнее окно (+62 с) и последние сырые окна (+31 с)
    e1 = get(EDGE)
    cpu.stop.set()
    lat = get(f"{SIM}/latency")["latency"]
    for p in phases:
        p["latency_ms"] = [x["latency_ms"] for x in lat if p["from"] <= x["t_pub"] - t0 <= p["to"] + 10]
        p["trials"] = sum(1 for x in get(f"{SIM}/truth")["overheat"] if p["from"] - 1 <= x["t"] <= p["to"] + 1)
    res = {"name": name, "seed": seed, "mode": mode, "t0": t0, "truth": get(f"{SIM}/truth"),
           "edge_before": e0, "edge_after": e1, "cloud": get(CLOUD), "phases": phases, "cpu": cpu.rows}
    d = {k: e1[k] - e0[k] for k in ("in_msgs", "in_bytes", "out_msgs", "out_bytes", "sent_bytes", "alarms",
                                     "raw_windows", "rule_cmds", "relayed_cmds", "fwd_msgs", "fwd_dropped")}
    res["delta"] = d
    up = d["out_bytes"] if mode == "edge" else d["sent_bytes"]
    say(f"{name}: вход {d['in_msgs']} сообщ. / {d['in_bytes'] / 1e6:.2f} МБ, в облако {up / 1e6:.3f} МБ "
        f"(×{d['in_bytes'] / max(up, 1):.1f}); алармов edge {d['alarms']}, облако {len(res['cloud']['cloud_alarms'])}; "
        + "; ".join(f"{p['label']}: {len(p['latency_ms'])}/{p['trials']} реакций" for p in phases))
    return res


PLAN = {"edge-1": (21, "edge"), "edge-2": (22, "edge"), "edge-3": (23, "edge"), "raw-1": (24, "raw")}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    RES.mkdir(exist_ok=True)
    for name in sys.argv[1:] or list(PLAN):
        r = run(name, *PLAN[name])
        (RES / f"{name}.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    sh("docker", "compose", "up", "-d", "--force-recreate", "sim", "edge")
    say("готово")


if __name__ == "__main__":
    main()
