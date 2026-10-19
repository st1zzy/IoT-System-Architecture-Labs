"""Графики Lab 6 по results/lab6.json: объём хранения и время запросов.

    python tools/plots.py --img report/img
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RES = Path(__file__).resolve().parent.parent / "results" / "lab6.json"
SER = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK2, GRID, SURFACE = "#52514e", "#e6e5e0", "#fcfcfb"


def style(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(color=GRID, lw=0.6, axis="y")
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK2, labelsize=9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default="report/img")
    img = Path(ap.parse_args().img)
    plt.rcParams.update({"font.family": "Times New Roman", "font.size": 10})
    r = json.loads(RES.read_text(encoding="utf-8"))
    n = r["load"]["narrow_rows"]
    per = {  # байт на одно измерение
        "узкая": r["load"]["narrow_bytes"] / n, "широкая JSONB": r["load"]["wide_bytes"] / n,
        "узкая сжатая": r["compression"]["telemetry"]["after"] / n,
        "широкая сжатая": r["compression"]["telemetry_wide"]["after"] / n,
        "Parquet (zstd)": r["cold"]["parquet_bytes"] / r["cold"]["rows"]}
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    bars = ax.bar(list(per), list(per.values()), color=[SER[0], SER[1], SER[0], SER[1], SER[2]], width=0.6)
    for b, v in zip(bars, per.values()):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.2, f"{v:.1f}".replace(".", ","), ha="center", fontsize=8, color=INK2)
    ax.set_ylabel("Байт на одно измерение", color=INK2)
    style(ax)
    fig.tight_layout(); fig.savefig(img / "fig_sizes.png", dpi=200, facecolor=SURFACE); plt.close(fig)

    names = {"last": "последнее\nзначение", "day_avg": "среднее\nза сутки", "anomalies_month": "аномалии\nза месяц",
             "device_year": "устройство\nза год"}
    fig, ax = plt.subplots(figsize=(7.0, 3.2))
    cfg = [("uncompressed", "narrow", "узкая"), ("uncompressed", "wide", "широкая"),
           ("compressed", "narrow", "узкая сжатая"), ("compressed", "wide", "широкая сжатая")]
    w = 0.2
    for i, (c, s, lab) in enumerate(cfg):
        vals = [r[f"queries_{c}"][q][s] for q in names]
        ax.bar([j + (i - 1.5) * w for j in range(4)], vals, w, label=lab, color=SER[i])
    ax.set_xticks(range(4), list(names.values()))
    ax.set_yscale("log")
    ax.set_ylabel("Время запроса, мс (медиана)", color=INK2)
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="lower left", bbox_to_anchor=(0, 1.0))
    style(ax)
    fig.tight_layout(); fig.savefig(img / "fig_queries.png", dpi=200, facecolor=SURFACE); plt.close(fig)
    print("графики: fig_sizes, fig_queries")


if __name__ == "__main__":
    main()
