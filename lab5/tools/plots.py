"""Графики Lab 5 по results/runs.json.

    python tools/plots.py --img report/img
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RES = Path(__file__).resolve().parent.parent / "results"
SER = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK2, GRID, SURFACE = "#52514e", "#e6e5e0", "#fcfcfb"


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default="report/img")
    img = Path(ap.parse_args().img)
    img.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "Times New Roman", "font.size": 10})
    runs = {r["name"]: r for r in json.loads((RES / "runs.json").read_text(encoding="utf-8"))}

    load = [runs[k] for k in runs if k.startswith("load-")]
    x = [r["offered"] for r in load]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.2))
    a1.plot(x, x, color=INK2, lw=1, ls="--", label="предложенная нагрузка")
    a1.plot(x, [r["throughput"] for r in load], color=SER[0], lw=1.8, marker="o", ms=5, label="записано в хранилище")
    a1.set_xlabel("Нагрузка, сообщений/с", color=INK2)
    a1.set_ylabel("Пропускная способность, сообщ./с", color=INK2)
    a1.legend(frameon=False, fontsize=8, loc="upper left")
    style(a1)
    for i, (k, lab) in enumerate((("p50", "p50"), ("p95", "p95"), ("p99", "p99"))):
        a2.plot(x, [r[k] for r in load], color=SER[i], lw=1.6, marker="o", ms=4, label=lab)
    a2.set_yscale("log")
    a2.set_xlabel("Нагрузка, сообщений/с", color=INK2)
    a2.set_ylabel("Задержка device_ts → запись, с", color=INK2)
    a2.legend(frameon=False, fontsize=8, loc="upper left")
    style(a2)
    save(fig, img / "fig_load.png")

    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    for i, k in enumerate(("load-1000", "load-2000", "load-3000", "load-4000")):
        s = runs[k]["samples"]
        ax.plot([p["t"] for p in s], [p["received"] - p["stored"] for p in s], color=SER[i], lw=1.6,
                label=f"{runs[k]['offered']} сообщ./с")
    ax.set_xlabel("Время от начала прогона, с", color=INK2)
    ax.set_ylabel("Принято, но не записано", color=INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    style(ax)
    save(fig, img / "fig_backlog.png")

    # batch-500 испорчен паузой хоста (скачок часов); та же конфигурация — load-1000
    bt = [runs["batch-1"], runs["batch-50"], runs["load-1000"]]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.0))
    lbl = [f"батч {r['batch']}" for r in bt]
    a1.bar(lbl, [r["queue_max"] for r in bt], color=SER[0], width=0.55)
    for j, r in enumerate(bt):
        a1.text(j, r["queue_max"] + 80, str(r["queue_max"]), ha="center", fontsize=8, color=INK2)
    a1.set_ylabel("Макс. очередь сервиса, сообщений", color=INK2)
    a1.set_title("нагрузка 1000 сообщ./с", fontsize=9, color=INK2)
    style(a1)
    w = 0.25
    for i, k in enumerate(("p50", "p95", "p99")):
        a2.bar([j + (i - 1) * w for j in range(3)], [r[k] for r in bt], w, color=SER[i], label=k)
    a2.set_xticks(range(3), lbl)
    a2.set_yscale("log")
    a2.set_ylabel("Задержка, с", color=INK2)
    a2.legend(frameon=False, fontsize=8, loc="upper right")
    style(a2)
    save(fig, img / "fig_batch.png")

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.0))
    for i, (k, lab) in enumerate((("bp-off", "без backpressure"), ("bp-on", "с backpressure"))):
        s = runs[k]["samples"]
        a1.plot([p["t"] for p in s], [p["queue"] for p in s], color=SER[i], lw=1.6, label=lab)
        a2.plot([p["t"] for p in s], [p["mem_mb"] for p in s], color=SER[i], lw=1.6, label=lab)
    a1.set_ylabel("Очередь сервиса, сообщений", color=INK2)
    a2.set_ylabel("Память ingest, МБ", color=INK2)
    for a in (a1, a2):
        a.set_xlabel("Время, с", color=INK2)
        a.legend(frameon=False, fontsize=8, loc="upper left")
        style(a)
    save(fig, img / "fig_backpressure.png")
    print("графики: fig_load, fig_backlog, fig_batch, fig_backpressure")


if __name__ == "__main__":
    main()
