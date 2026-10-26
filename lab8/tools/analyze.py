"""Обработка прогонов Lab 8 → results/summary.json и графики report/img/fig_*.png.

Перегрев — управляющее событие сценария, а не аномалия датчика. Поэтому из оценки детектора исключаются
и алармы, и истинные эпизоды по температуре того же устройства, пересекающиеся с окном перегрева
(−2…+130 с: после реального события детектор законно держит паузу HOLDOFF). Пробы реакции, попавшие на
устройство с предыдущей пробой в пределах 7 с, зависимы (перегрев продлевается без нового перехода через
порог) и не учитываются. Число исключённых выводится.
    python tools/analyze.py --img report/img
"""

import argparse
import json
import math
import statistics as st
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "common"))
from evaluate import agg_detector, match  # noqa: E402

RES = ROOT / "results"
SER = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6"]
INK2, GRID, SURFACE = "#52514e", "#e6e5e0", "#fcfcfb"
T95 = {2: 12.706, 3: 4.303}


def load(n: str) -> dict | None:
    p = RES / f"{n}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def pct(v, p):
    s = sorted(v)
    return s[min(len(s) - 1, round(p / 100 * (len(s) - 1)))] if s else float("nan")


def control_masked(r: dict, dev: str, metric: str, t: float) -> bool:
    return metric == "temperature" and any(o["dev"] == dev and o["t"] - 2 <= t <= o["t"] + 130
                                           for o in r["truth"]["overheat"])


def truth_eval(r: dict) -> tuple[list[dict], list[dict]]:
    keep, drop = [], []
    for e in r["truth"]["episodes"]:
        hit = e["metric"] == "temperature" and any(
            o["dev"] == e["dev"] and e["start"] <= o["t"] + 130 and o["t"] - 2 <= e["end"] + 30
            for o in r["truth"]["overheat"])
        (drop if hit else keep).append(e)
    return keep, drop


def drop_alarms(alarms: list[dict], dropped: list[dict]) -> list[dict]:
    """Алармы, относящиеся к исключённым эпизодам, тоже не оцениваются."""
    return [a for a in alarms if not any(e["dev"] == a["dev"] and e["metric"] == a["metric"]
                                         and e["start"] - 5 <= a["t"] <= e["end"] + 30 for e in dropped)]


def dependent_trials(r: dict, lo: float, hi: float) -> int:
    oh = sorted(r["truth"]["overheat"], key=lambda o: o["t"])
    return sum(1 for i, o in enumerate(oh) if lo - 1 <= o["t"] <= hi + 1 and any(
        p["dev"] == o["dev"] and 0 < o["t"] - p["t"] <= 7 for p in oh[:i]))


def alarms_rel(r: dict, key: str) -> list[dict]:
    t0 = r["t0"]
    out = []
    for a in r["cloud"][key]:
        t = a["ts"] / 1000 - t0
        if not control_masked(r, a["dev"], a["metric"], t):
            out.append({"dev": a["dev"], "metric": a["metric"], "t": t, "type": a["type"],
                        "delivery_s": a["its"] - a["ts"] / 1000})
    return out


def windows(r: dict) -> dict:
    w = {}
    for a in r["cloud"]["agg"]:
        if a["n"] < 55:                                   # неполные окна на границах прогона
            continue
        for metric, (mn, mx, av, sd, p95) in a["m"].items():
            t0w = a["w0"] - r["t0"]
            if control_masked(r, a["dev"], metric, t0w + 30) or control_masked(r, a["dev"], metric, t0w + 59):
                continue
            w.setdefault((a["dev"], metric), []).append({"t0": t0w, "min": mn, "max": mx, "avg": av, "std": sd,
                                                        "p95": p95})
    return w


def summarize() -> dict:
    out = {"runs": {}}
    for name in ("edge-1", "edge-2", "edge-3", "raw-1"):
        r = load(name)
        if not r:
            continue
        truth, dropped = truth_eval(r)
        excluded = len(dropped)
        d = r["delta"]
        up = d["out_bytes"] if r["mode"] == "edge" else d["sent_bytes"]
        x = {"mode": r["mode"], "seed": r["seed"], "in_msgs": d["in_msgs"], "in_bytes": d["in_bytes"],
             "up_bytes": up, "factor": d["in_bytes"] / max(up, 1),
             "cloud_bytes": r["cloud"]["bytes"], "cloud_msgs": r["cloud"]["msgs"],
             "edge_cpu": st.mean([c["cpu"] for c in r["cpu"] if c["name"] == "lab8-edge"] or [0]),
             "cloud_cpu": st.mean([c["cpu"] for c in r["cpu"] if c["name"] == "lab8-cloud-svc"] or [0]),
             "excluded_episodes": excluded,
             "phases": [{"label": p["label"], "trials": p["trials"], "reacted": len(p["latency_ms"]),
                         "dependent": dependent_trials(r, p["from"], p["to"]),
                         "p50": pct(p["latency_ms"], 50), "p95": pct(p["latency_ms"], 95),
                         "max": max(p["latency_ms"]) if p["latency_ms"] else None, "latency_ms": p["latency_ms"]}
                        for p in r["phases"]]}
        if r["mode"] == "edge":
            al = drop_alarms(alarms_rel(r, "alarms"), dropped)
            x["edge_detector"] = match(truth, al)
            x["agg_detector"] = match(truth, drop_alarms(agg_detector(windows(r)), dropped))
            x["alarm_delivery_s"] = {"p50": pct([a["delivery_s"] for a in al], 50),
                                     "max": max((a["delivery_s"] for a in al), default=None)}
            x["raw_windows"] = len(r["cloud"]["rawwin"])
            x["raw_window_points"] = st.mean([w["n"] for w in r["cloud"]["rawwin"]] or [0])
            keys = {(a["dev"], a["w0"]) for a in r["cloud"]["agg"]}
            w0s = sorted({a["w0"] for a in r["cloud"]["agg"]})
            full = [w for w in w0s if w - r["t0"] >= 0 and w + 60 <= r["t0"] + 600]
            x["agg_expected"] = 200 * len(full)
            x["agg_received"] = sum(1 for k in keys if k[1] in full)
            if name == "edge-3":
                x["outage_alarms"] = [{"t": a["t"], "delivery_s": a["delivery_s"]} for a in al if 240 <= a["t"] <= 420]
        else:
            x["cloud_detector"] = match(truth, drop_alarms(alarms_rel(r, "cloud_alarms"), dropped))
            x["fwd_dropped"] = d["fwd_dropped"]
        out["runs"][name] = x
    e = [out["runs"][n] for n in ("edge-1", "edge-2", "edge-3") if n in out["runs"]]
    if e:
        def ci(v):
            return (st.mean(v), T95.get(len(v), 1.96) * st.stdev(v) / math.sqrt(len(v)) if len(v) > 1 else 0)
        out["edge_total"] = {
            "factor": ci([x["factor"] for x in e]),
            "edge_recall": ci([x["edge_detector"]["recall"] for x in e]),
            "edge_precision": ci([x["edge_detector"]["precision"] for x in e]),
            "agg_recall": ci([x["agg_detector"]["recall"] for x in e]),
            "agg_precision": ci([x["agg_detector"]["precision"] for x in e]),
            "episodes": sum(x["edge_detector"]["episodes"] for x in e),
            "found": sum(x["edge_detector"]["found"] for x in e),
            "alarms": sum(x["edge_detector"]["alarms"] for x in e),
            "true_alarms": sum(x["edge_detector"]["true_alarms"] for x in e),
            "agg_found": sum(x["agg_detector"]["found"] for x in e),
            "agg_alarms": sum(x["agg_detector"]["alarms"] for x in e),
            "agg_true": sum(x["agg_detector"]["true_alarms"] for x in e),
        }
        bt = {}
        for x in e:
            for kind, det in (("edge", "edge_detector"), ("agg", "agg_detector")):
                for t, b in x[det]["by_type"].items():
                    z = bt.setdefault(kind, {}).setdefault(t, {"n": 0, "found": 0, "delays": []})
                    z["n"] += b["n"]
                    z["found"] += b["found"]
                    z["delays"] += b["delays"]
        for kind in bt.values():
            for z in kind.values():
                z["recall"] = z["found"] / z["n"]
                z["delay_median"] = st.median(z["delays"]) if z["delays"] else None
                z.pop("delays")
        out["by_type"] = bt
    return out


def style(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(color=GRID, lw=0.6, axis="y")
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


def plots(s: dict, img: Path) -> None:
    plt.rcParams.update({"font.family": "Times New Roman", "font.size": 10})
    runs = s["runs"]
    # 1. трафик: КБ/мин на входе edge и в облако
    if "edge-1" in runs and "raw-1" in runs:
        fig, ax = plt.subplots(figsize=(6.4, 3.0))
        e, r = runs["edge-1"], runs["raw-1"]
        mins = 600 / 60
        cats = ["вход edge\n(200 устр. × 1 Гц)", "в облако:\nвсё сырое", "в облако: edge\n(агрегаты+алармы+окна)"]
        vals = [e["in_bytes"] / mins / 1024, r["up_bytes"] / mins / 1024, e["up_bytes"] / mins / 1024]
        bars = ax.bar(cats, vals, color=[INK2, SER[1], SER[0]], width=0.55)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v * 1.15, f"{v:,.0f}".replace(",", " ") + " КБ/мин",
                    ha="center", fontsize=8, color=INK2)
        ax.set_yscale("log")
        ax.set_ylabel("КБ в минуту (лог. шкала)", color=INK2)
        style(ax)
        save(fig, img / "fig_traffic.png")
    # 2. recall / precision
    t = s.get("edge_total")
    if t:
        fig, ax = plt.subplots(figsize=(6.4, 3.0))
        groups = [("edge: детектор\nпо сырому потоку", t["edge_recall"], t["edge_precision"]),
                  ("облако: только\nагрегаты 60 с", t["agg_recall"], t["agg_precision"])]
        if "raw-1" in runs:
            c = runs["raw-1"]["cloud_detector"]
            groups.append(("облако: всё сырое\n(тот же детектор)", (c["recall"], 0), (c["precision"], 0)))
        for i, (lab, rc, pr) in enumerate(groups):
            ax.bar(i - 0.18, 100 * rc[0], 0.34, yerr=100 * rc[1], color=SER[0], capsize=3,
                   label="recall" if i == 0 else None)
            ax.bar(i + 0.18, 100 * pr[0], 0.34, yerr=100 * pr[1], color=SER[2], capsize=3,
                   label="precision" if i == 0 else None)
            ax.text(i - 0.18, 100 * rc[0] + 2.5, f"{100 * rc[0]:.1f}".replace(".", ","), ha="center", fontsize=8, color=INK2)
            ax.text(i + 0.18, 100 * pr[0] + 2.5, f"{100 * pr[0]:.1f}".replace(".", ","), ha="center", fontsize=8, color=INK2)
        ax.axhline(95, color=INK2, lw=0.8, ls="--")
        ax.set_xticks(range(len(groups)), [g[0] for g in groups], fontsize=8)
        ax.set_ylim(0, 112)
        ax.set_ylabel("%", color=INK2)
        ax.legend(frameon=False, fontsize=8, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2)
        style(ax)
        save(fig, img / "fig_detection.png")
    # 3. задержка реакции
    ph = [p for n in ("edge-1", "edge-2", "edge-3", "raw-1") if n in runs for p in runs[n]["phases"]]
    if ph:
        fig, ax = plt.subplots(figsize=(7.0, 3.2))
        for i, p in enumerate(ph):
            v = p["latency_ms"]
            if v:
                ax.boxplot([v], positions=[i], widths=0.5, showfliers=True, patch_artist=True,
                           boxprops={"facecolor": SER[0] if p["label"].startswith("edge") else SER[1], "alpha": 0.6},
                           medianprops={"color": "#0b0b0b"})
                ax.text(i, max(v) * 1.25, f"{p['p50']:.0f} мс".replace(".", ","), ha="center", fontsize=8, color=INK2)
            else:
                ax.text(i, 3, f"нет реакции\n0 из {p['trials']}", ha="center", fontsize=8, color=SER[1])
        ax.set_xticks(range(len(ph)), [p["label"].replace(", ", ",\n") for p in ph], fontsize=8)
        ax.set_yscale("log")
        ax.set_ylabel("Задержка реакции, мс (лог.)", color=INK2)
        style(ax)
        save(fig, img / "fig_latency.png")
    # 4. доставка алармов при обрыве (edge-3)
    if "edge-3" in runs:
        r = load("edge-3")
        al = alarms_rel(r, "alarms")
        fig, ax = plt.subplots(figsize=(6.4, 2.8))
        ax.axvspan(240, 420, color=SER[1], alpha=0.12, label="обрыв WAN")
        ax.scatter([a["t"] for a in al], [a["delivery_s"] for a in al], s=14, color=SER[0], label="аларм")
        ax.set_xlabel("Время возникновения аларма, с от начала прогона", color=INK2)
        ax.set_ylabel("Доставка в облако, с", color=INK2)
        ax.legend(frameon=False, fontsize=8, loc="upper right")
        style(ax)
        save(fig, img / "fig_outage.png")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default="report/img")
    img = Path(ap.parse_args().img)
    img.mkdir(parents=True, exist_ok=True)
    sys.stdout.reconfigure(encoding="utf-8")
    s = summarize()
    (RES / "summary.json").write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    for n, x in s["runs"].items():
        print(f"{n} ({x['mode']}, seed {x['seed']}): вход {x['in_bytes'] / 1e6:.2f} МБ, в облако {x['up_bytes'] / 1e6:.3f} МБ, "
              f"×{x['factor']:.1f}; CPU edge {x['edge_cpu']:.1f} %, cloud-svc {x['cloud_cpu']:.1f} %")
        for k in ("edge_detector", "agg_detector", "cloud_detector"):
            if k in x:
                m = x[k]
                bt = ", ".join(f"{t} {b['found']}/{b['n']} ({b['delay_median']} с)" for t, b in m["by_type"].items())
                print(f"   {k}: recall {m['recall']:.3f}, precision {m['precision']:.3f} "
                      f"({m['true_alarms']}/{m['alarms']} алармов) | {bt}")
        for p in x["phases"]:
            print(f"   {p['label']}: реакций {p['reacted']} из {p['trials'] - p['dependent']} независимых проб "
                  f"(зависимых {p['dependent']}), p50 {p['p50']:.1f} мс, p95 {p['p95']:.1f} мс")
        print(f"   исключено эпизодов (пересечение с перегревом): {x['excluded_episodes']}")
        if "agg_expected" in x:
            print(f"   агрегаты: {x['agg_received']}/{x['agg_expected']}; сырых окон {x['raw_windows']} "
                  f"(~{x['raw_window_points']:.0f} точек); доставка алармов p50 {x['alarm_delivery_s']['p50']:.2f} с, "
                  f"макс {x['alarm_delivery_s']['max']:.1f} с")
    if "edge_total" in s:
        print("итого edge:", json.dumps(s["edge_total"], ensure_ascii=False))
        print("по типам:", json.dumps(s["by_type"], ensure_ascii=False))
    plots(s, img)


if __name__ == "__main__":
    main()
