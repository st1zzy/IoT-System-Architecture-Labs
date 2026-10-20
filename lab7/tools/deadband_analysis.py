"""Офлайн-анализ deadband на сыром потоке шлюза (results/raw_trace.csv) тем же кодом фильтра, что в шлюзе.

Для каждой настройки: доля опубликованных значений, экономия трафика и ошибка восстановления —
приёмник держит последнее опубликованное значение (sample-and-hold), ошибка = |сырое − удерживаемое|.
    python tools/deadband_analysis.py
"""

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateway"))
from filters import Deadband  # noqa: E402

CFG = yaml.safe_load(open(ROOT / "gateway" / "mapping.yaml", encoding="utf-8"))
SPEC = {r["tag"]: r for r in CFG["modbus"]["registers"]} | {f["tag"]: f for f in CFG["legacy_mqtt"]["fields"]}
CONFIGS = [("off", 1.0)] + [("abs", k) for k in (0.5, 1, 2, 4)] + [("pct", k) for k in (0.5, 1, 2, 5)]
RECORD_BYTES = 120.5        # средняя запись канонической модели в JSON, измерено по сырому потоку


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    series: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)
    with open(ROOT / "results" / "raw_trace.csv", encoding="utf-8") as f:
        for dts, asset, tag, v in csv.reader(f):
            series[(asset, tag)].append((int(dts), float(v)))
    for s in series.values():
        s.sort()
    n_raw = sum(len(s) for s in series.values())
    dur = (max(s[-1][0] for s in series.values()) - min(s[0][0] for s in series.values())) / 1000
    out = []
    print(f"Сырой поток: {n_raw} значений, {len(series)} тегов, {dur:.0f} с ({n_raw / dur:.1f} значений/с)")
    print(f"{'режим':<12}{'опубл.':>8}{'доля':>8}{'экономия':>10}{'МБ/сут':>9}   ошибка восстановления: max (RMSE) по тегам")
    for mode, k in CONFIGS:
        pub, err = 0, defaultdict(list)
        peak = {}
        for (asset, tag), s in series.items():
            spec = SPEC[tag]
            f = Deadband(mode, spec.get("deadband_abs", 0), spec.get("deadband_pct", 0), CFG["heartbeat_s"],
                         spec.get("discrete", False), k)
            held = None
            for ts, v in s:
                if f.check(v, ts):
                    held = v
                    pub += 1
                err[tag].append(abs(v - held))
                if tag == "bearing_temp":
                    peak[asset] = max(peak.get(asset, (-1e9, -1e9))[0], v), max(peak.get(asset, (-1e9, -1e9))[1], held)
        e = {t: (max(v), math.sqrt(sum(x * x for x in v) / len(v))) for t, v in err.items()}
        name = "без фильтра" if mode == "off" else f"{mode} ×{k:g}"
        mb = pub / dur * RECORD_BYTES * 86400 / 1e6
        print(f"{name:<12}{pub:>8}{100 * pub / n_raw:>7.1f}%{100 * (1 - pub / n_raw):>9.1f}%{mb:>9.1f}   "
              + "; ".join(f"{t} {m:.2f} ({r:.2f})" for t, (m, r) in sorted(e.items())))
        out.append({"mode": mode, "scale": k, "published": pub, "raw": n_raw, "share": pub / n_raw,
                    "saving": 1 - pub / n_raw, "mb_day": mb, "duration_s": dur,
                    "error": {t: {"max": m, "rmse": r, "unit": SPEC[t]["unit"]} for t, (m, r) in e.items()},
                    "overheat_peak": peak.get("pump-02")})
    (ROOT / "results" / "deadband.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
