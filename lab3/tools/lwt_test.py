"""Время появления LWT после гибели клиента (запускается в lab3-device, наблюдатель — в backend).

kill   — kill -9 процесса: ОС закрывает сокет, брокер сразу видит разрыв TCP;
silent — устройство «пропадает» без закрытия соединения (потеря питания, обрыв радиоканала):
         netem 100 % потерь; брокер ждёт 1,5 × keep-alive.
    python tools/lwt_test.py --keepalive 5 10 20 --repeats 3
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import netem  # noqa: E402

EVENTS = Path("/lab/results/lwt/events.jsonl")


def wait_status(client: str, status: str, after: float, timeout: float) -> float | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if EVENTS.exists():
            for line in EVENTS.read_text(encoding="utf-8").splitlines():
                e = json.loads(line)
                if e["client"] == client and e["status"] == status and e["t"] >= after:
                    return e["t"]
        time.sleep(0.05)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keepalive", type=int, nargs="+", default=[5, 10, 20])
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    results = []
    for k in args.keepalive:
        for mode in ("kill", "silent"):
            for r in range(1, args.repeats + 1):
                cid = f"lwt-k{k}-{mode}-{r}"
                t_start = time.time()
                p = subprocess.Popen([sys.executable, "tools/lwt_client.py", "--id", cid, "--keepalive", str(k)])
                if wait_status(cid, "online", t_start, 10) is None:
                    raise RuntimeError(f"{cid}: нет статуса online")
                time.sleep(1.5)
                t0 = time.time()
                if mode == "kill":
                    os.kill(p.pid, signal.SIGKILL)
                else:
                    netem.apply(100)
                t1 = wait_status(cid, "offline", t0, 3 * k + 15)
                if mode == "silent":
                    os.kill(p.pid, signal.SIGKILL)
                    netem.clear()
                p.wait()
                dt = None if t1 is None else t1 - t0
                results.append({"client": cid, "keepalive": k, "mode": mode, "repeat": r, "detect_s": dt})
                print(f"{cid}: keep-alive {k} с, {'kill -9' if mode == 'kill' else 'тихий отказ'} → "
                      f"offline через {dt:.3f} с ({dt / k:.2f} × keep-alive)" if dt is not None else
                      f"{cid}: LWT не получен", flush=True)
                time.sleep(1)
    Path("/lab/results/lwt/lwt.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
