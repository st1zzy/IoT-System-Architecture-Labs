"""Эксперименты Lab 5 (запуск с хоста): нагрузка, батчинг, backpressure → results/runs.json.

    python tools/run_experiments.py
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results"
ENV = dict(os.environ, MSYS_NO_PATHCONV="1")
DURATION = 30
# Git Bash (не bash.exe из System32 — тот запускает WSL)
BASH = shutil.which("bash", path=r"C:\Program Files\Git\bin") or "bash"


def sh(cmd: str) -> str:
    # через bash: на Windows shell=True означает cmd.exe (другие кавычки и нет префикса VAR=…)
    return subprocess.run([BASH, "-c", cmd], capture_output=True, text=True, encoding="utf-8",
                          env=ENV, cwd=ROOT).stdout


def http(path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(f"http://localhost:8000{path}", method=method)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def sys_counter(topic: str) -> int:
    out = sh(f"docker exec lab5-mosquitto mosquitto_sub -t '$SYS/broker/{topic}' -C 1 -W 8")
    return int(out.strip() or 0)


def mem_mb(s: str) -> float:
    v = s.split("/")[0].strip()
    num = float("".join(ch for ch in v if ch.isdigit() or ch == "."))
    return num / 1024 if "KiB" in v else num * 1024 if "GiB" in v else num


def run(name: str, devices: int, batch: int, bp: int) -> dict:
    env = f"MAX_BATCH={batch} MAX_DELAY=2.0 BACKPRESSURE={bp}"
    sh(f"{env} docker compose up -d --force-recreate ingest")
    for _ in range(60):
        try:
            if http("/health")["max_batch"] == batch:
                break
        except OSError:
            pass
        time.sleep(1)
    time.sleep(2)
    dropped0, bytes0 = sys_counter("publish/messages/dropped"), sys_counter("bytes/received")
    http("/reset", "POST")
    samples, stop = [], threading.Event()

    def sampler():
        while not stop.is_set():
            st = http("/stats")
            ds = sh("docker stats --no-stream --format '{{.CPUPerc}};{{.MemUsage}}' lab5-ingest").strip().split(";")
            samples.append({"t": st["seconds"], "queue": st["queue_depth"], "stored": st["stored"],
                            "received": st["received"], "cpu": float(ds[0].rstrip("%")), "mem_mb": mem_mb(ds[1])})
            time.sleep(1)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    gen = sh(f"docker exec lab5-loadgen python tools/loadgen.py --devices {devices} --duration {DURATION} --workers 6")
    sent = int(gen.split("отправлено ")[1].split()[0])
    t_end, last = time.time(), -1
    while time.time() - t_end < 60:                       # дожидаемся, пока конвейер «доест» хвост
        s = http("/stats")["stored"]
        if s == last:
            break
        last = s
        time.sleep(4)
    stop.set(); th.join()
    st = http("/stats")
    dropped = sys_counter("publish/messages/dropped") - dropped0
    nbytes = sys_counter("bytes/received") - bytes0
    work = [x for x in samples if x["t"] <= DURATION + 2]
    res = {"name": name, "devices": devices, "offered": devices, "batch": batch, "backpressure": bp, "sent": sent,
           "received": st["received"], "stored": st["stored"], "lost": sent - st["stored"], "broker_dropped": dropped,
           "throughput": st["stored"] / DURATION, "p50": st["p50"], "p95": st["p95"], "p99": st["p99"],
           "queue_max": st["queue_max"], "batch_mean": st["batch_mean"],
           "cpu_max": max((x["cpu"] for x in work), default=0), "mem_max_mb": max((x["mem_mb"] for x in samples), default=0),
           "bytes_per_msg_broker_in": nbytes / sent if sent else 0, "samples": samples}
    print(f"{name:<12} N={devices:<5} batch={batch:<3} bp={bp}: отправлено {sent}, записано {st['stored']} "
          f"(потеряно {res['lost']}, брокер отбросил {dropped}); {res['throughput']:.0f} сообщ./с; "
          f"p50 {st['p50']:.2f} p95 {st['p95']:.2f} p99 {st['p99']:.2f} с; очередь макс {st['queue_max']}; "
          f"батч ср. {st['batch_mean']:.0f}; CPU макс {res['cpu_max']:.0f} %, RAM макс {res['mem_max_mb']:.0f} МБ",
          flush=True)
    return res


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    OUT.mkdir(exist_ok=True)
    plan = [(f"load-{n}", n, 500, 0) for n in (100, 500, 1000, 2000, 3000, 4000)]
    plan += [(f"batch-{b}", 1000, b, 0) for b in (1, 50, 500)]
    plan += [("bp-off", 1500, 1, 0), ("bp-on", 1500, 1, 1)]
    runs = [run(*p) for p in plan]
    (OUT / "runs.json").write_text(json.dumps(runs, indent=1, ensure_ascii=False), encoding="utf-8")
    sh("docker compose up -d --force-recreate ingest")


if __name__ == "__main__":
    main()
