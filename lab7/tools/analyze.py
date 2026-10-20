"""Обработка результатов Lab 7: метрики по циклам обрыва и графики.

    python tools/analyze.py --img report/img      → results/summary.json, report/img/fig_*.png
"""

import argparse
import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RES = Path(__file__).resolve().parent.parent / "results"
SER = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK2, GRID, SURFACE = "#52514e", "#e6e5e0", "#fcfcfb"


def load(name: str) -> dict | None:
    p = RES / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def pct(v: list[float], p: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, round(p / 100 * (len(s) - 1)))] if s else float("nan")


def cycle_metrics(r: dict, ev: dict) -> dict:
    arr = r["arrivals"]
    gw = r["gw"]
    # обнаружение обрыва: первый отсчёт после down с connected = false
    det = next((g["t"] for g in gw if g["t"] >= ev["down"] and not g["connected"]), None)
    rec = next((g["t"] for g in gw if g["t"] >= ev["up"] and g["connected"]), None)
    win = [a for a in arr if ev["up"] * 1000 <= a[4] <= ev["resynced"] * 1000 + 2000]
    per_s = Counter(a[4] // 1000 for a in win)
    backfilled = [a for a in arr if a[1] == 1 and ev["down"] * 1000 <= a[2] < ev["up"] * 1000]
    m = {"label": ev["label"], "buffer_at_up": ev["buffer_at_up"], "resync_s": ev["resync_s"],
         "detect_s": det - ev["down"] if det else None, "reconnect_s": rec - ev["up"] if rec else None,
         "peak_records_s": max(per_s.values()) if per_s else 0,
         "mean_records_s": len(win) / max(1.0, ev["resynced"] - ev["up"]),
         "backfilled": len(backfilled),
         "max_delay_s": max((a[4] - a[2]) / 1000 for a in backfilled) if backfilled else None}
    if "overheat" in ev:
        al = [a for a in arr if a[1] == 0 and ev["overheat"] * 1000 - 2000 <= a[2] <= ev["up"] * 1000]
        if al:
            a = min(al, key=lambda x: x[2])
            after_up = sorted(x[4] for x in arr if x[4] >= ev["up"] * 1000 and x[1] == 1)
            m["alarm"] = {"raised_dts": a[2], "arrived_after_up_s": (a[4] / 1000 - ev["up"]),
                          "normal_records_before": sum(1 for t in after_up if t < a[4]),
                          "delay_s": (a[4] - a[2]) / 1000}
    return m


def summarize() -> dict:
    out = {}
    for name in ("main", "limited_raw", "unlimited", "sendmode", "overflow"):
        r = load(name)
        if not r:
            continue
        cyc = [cycle_metrics(r, ev) for ev in r["cycles"]]
        cpu = [c for c in r["cpu"] if any(ev["up"] <= c["t"] <= ev["resynced"] + 3 for ev in r["cycles"])]
        out[name] = {k: r[k] for k in ("expected", "received_unique", "missing", "dups", "dropped_by_gateway")}
        out[name]["missing_seqs"] = r["missing_seqs"][:30]
        out[name]["cycles"] = cyc
        out[name]["cpu_backfill_max"] = {n: max((c["cpu"] for c in cpu if c["name"] == n), default=0)
                                         for n in ("lab7-north", "lab7-ingest", "lab7-db")}
    r = load("main")
    if r:
        arr = r["arrivals"]
        online = [a for a in arr if a[1] == 1 and not any(ev["down"] * 1000 - 2000 <= a[2] <= ev["resynced"] * 1000
                                                          for ev in r["cycles"])]
        lat = [(a[4] - a[2]) / 1000 for a in online]
        al = [a for a in arr if a[1] == 0 and abs(a[2] - r["online_alarm"] * 1000) < 30000]
        out["latency_online"] = {"normal_p50": pct(lat, 50), "normal_p95": pct(lat, 95), "normal_max": max(lat),
                                 "alarm_delays": [(a[4] - a[2]) / 1000 for a in al], "n": len(lat)}
    c = load("crash")
    if c:
        out["crash"] = {k: c[k] for k in ("expected", "received_unique", "missing", "dups", "dropped_by_gateway")}
        out["crash"]["events"] = c["events"]
        out["crash"]["gateway_log"] = c["gateway_log"]
    return out


def style(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK2, labelsize=9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)


def save(fig, p) -> None:
    fig.tight_layout()
    fig.savefig(p, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def plots(img: Path) -> None:
    plt.rcParams.update({"font.family": "Times New Roman", "font.size": 10})
    r = load("main")
    if r:
        t0 = r["gw"][0]["t"]
        fig, ax = plt.subplots(figsize=(7.2, 3.0))
        ax.plot([(g["t"] - t0) / 60 for g in r["gw"]], [g["buffer"] for g in r["gw"]], color=SER[0], lw=1.4)
        for i, ev in enumerate(r["cycles"]):
            ax.axvspan((ev["down"] - t0) / 60, (ev["up"] - t0) / 60, color=SER[1], alpha=0.12,
                       label="обрыв WAN" if i == 0 else None)
            if "overheat" in ev:
                ax.axvline((ev["overheat"] - t0) / 60, color=SER[1], lw=0.8, ls="--",
                           label="перегрев насоса 1 (аларм)" if i == 0 else None)
        ax.set_xlabel("Время эксперимента, мин", color=INK2)
        ax.set_ylabel("Записей в буфере", color=INK2)
        ax.legend(frameon=False, fontsize=8, loc="upper left")
        style(ax)
        save(fig, img / "fig_buffer.png")

    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.0), sharey=True)
    for ax, name, lab in zip(axes, ("limited_raw", "unlimited"), ("ограничение 200 записей/с", "без ограничения")):
        x = load(name)
        if not x:
            continue
        ev = x["cycles"][0]
        cnt = Counter(a[4] // 1000 - int(ev["up"]) for a in x["arrivals"])
        ts = list(range(-20, int(ev["resynced"] - ev["up"]) + 25))
        ax.bar(ts, [cnt.get(t, 0) for t in ts], width=1.0, color=SER[0] if name == "limited_raw" else SER[1])
        ax.axhline(200, color=INK2, lw=0.8, ls="--")
        ax.set_title(lab, fontsize=9, color=INK2)
        ax.set_xlabel("Секунды от восстановления WAN", color=INK2)
        style(ax)
    axes[0].set_ylabel("Записей в секунду на брокере", color=INK2)
    axes[0].set_yscale("log")
    save(fig, img / "fig_backfill.png")

    db = json.loads((RES / "deadband.json").read_text(encoding="utf-8")) if (RES / "deadband.json").exists() else None
    if db:
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.0))
        names = ["без\nфильтра" if d["mode"] == "off" else f"{d['mode']}\n×{d['scale']:g}".replace(".", ",") for d in db]
        a1.bar(range(len(db)), [100 * d["saving"] for d in db],
               color=[INK2 if d["mode"] == "off" else SER[0] if d["mode"] == "abs" else SER[2] for d in db])
        a1.set_xticks(range(len(db)), names, fontsize=7)
        a1.set_ylabel("Экономия трафика, %", color=INK2)
        style(a1)
        for i, tag in enumerate(("bearing_temp", "pressure", "temperature")):
            a2.plot(range(len(db)), [d["error"][tag]["max"] for d in db], color=SER[i], lw=1.5, marker="o", ms=4,
                    label=f"{tag}, {db[0]['error'][tag]['unit']}")
        a2.set_xticks(range(len(db)), names, fontsize=7)
        a2.set_ylabel("Макс. ошибка восстановления", color=INK2)
        a2.legend(frameon=False, fontsize=7, loc="upper left")
        style(a2)
        save(fig, img / "fig_deadband.png")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default="report/img")
    img = Path(ap.parse_args().img)
    img.mkdir(parents=True, exist_ok=True)
    sys.stdout.reconfigure(encoding="utf-8")
    s = summarize()
    (RES / "summary.json").write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    for name, v in s.items():
        if "cycles" in v:
            print(f"{name}: ожидалось {v['expected']}, получено {v['received_unique']}, потеряно {v['missing']}, "
                  f"дублей {v['dups']}, вытеснено шлюзом {v['dropped_by_gateway']}; CPU при backfill {v['cpu_backfill_max']}")
            for c in v["cycles"]:
                print("   " + ", ".join(f"{k}={round(x, 2) if isinstance(x, float) else x}" for k, x in c.items()))
    for k in ("latency_online", "crash"):
        if k in s:
            print(k, json.dumps({a: b for a, b in s[k].items() if a != "gateway_log"}, ensure_ascii=False))
    plots(img)


if __name__ == "__main__":
    main()
