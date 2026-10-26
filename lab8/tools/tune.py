"""Офлайн-проверка детекторов на модели (без Docker): recall/precision edge-детектора и детектора по агрегатам.

    python tools/tune.py --seeds 1 2 3 --duration 600 --anomalies 60
"""

import argparse
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from detect import Detector  # noqa: E402
from evaluate import agg_detector, match  # noqa: E402
from model import METRICS, Device, schedule  # noqa: E402


def run(seed: int, n_dev: int, duration: int, n_anom: int) -> dict:
    rng = random.Random(seed + 1000)
    devs = [Device(i, rng) for i in range(n_dev)]
    truth = schedule(seed, n_dev, duration, n_anom)
    starts: dict[int, list] = {}
    for a in truth:
        starts.setdefault(a.start, []).append(a)
    det = {(d, m): Detector() for d in range(n_dev) for m in METRICS}
    raw = {(d, m): [] for d in range(n_dev) for m in METRICS}
    alarms = []
    for k in range(duration):
        for a in starts.get(k, []):
            devs[a.dev].anom[a.metric] = a
        for d, dev in enumerate(devs):
            for m, v in dev.sample(k).items():
                raw[(d, m)].append(v)
                if det[(d, m)].update(v, k):
                    alarms.append({"dev": f"dev-{d:03d}", "metric": m, "t": k})
    windows = {}
    for (d, m), vs in raw.items():
        arr = np.array(vs)
        windows[(f"dev-{d:03d}", m)] = [
            {"t0": w * 60, "min": float(x.min()), "max": float(x.max()), "avg": float(x.mean()),
             "std": float(x.std()), "p95": float(np.percentile(x, 95))}
            for w in range(duration // 60) for x in [arr[w * 60:(w + 1) * 60]]]
    tr = [a.as_dict() for a in truth]
    return {"edge": match(tr, alarms), "agg": match(tr, agg_detector(windows))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--devices", type=int, default=200)
    ap.add_argument("--duration", type=int, default=600)
    ap.add_argument("--anomalies", type=int, default=60)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    for s in a.seeds:
        r = run(s, a.devices, a.duration, a.anomalies)
        for name, x in r.items():
            bt = ", ".join(f"{t}: {b['found']}/{b['n']} (медиана {b['delay_median']} с)" for t, b in x["by_type"].items())
            print(f"seed {s} {name:<4}: recall {x['recall']:.3f} precision {x['precision']:.3f} "
                  f"(алармов {x['alarms']}, верных {x['true_alarms']}) | {bt}")
            for miss in x["missed"][:4]:
                print("     пропущен:", miss)


if __name__ == "__main__":
    main()
