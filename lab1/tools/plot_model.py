"""Сутки работы модели датчиков (17 280 опросов по 5 с) с отметками флагов валидации.

    python tools/plot_model.py --out report/img/fig_model.png
"""

import argparse
import random
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "simulator"))
from device import Device  # noqa: E402

LINE, SPIKE, STUCK, BAD = "#2a78d6", "#eb6834", "#6250d6", "#e34948"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"
PANELS = [("temperature", "Температура, °C"), ("humidity", "Влажность, % RH"),
          ("pressure", "Давление, гПа")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=3)
    args = ap.parse_args()
    random.seed(args.seed)
    dev = Device("room-sensor-01")
    t0 = 1792281600.0 - 5 * 3600                      # 00:00 по Астане (UTC+5)
    n = 86400 // 5
    msgs = [dev.sample(t0 + i * 5) for i in range(n)]
    hours = [i * 5 / 3600 for i in range(n)]

    plt.rcParams.update({"font.family": "Times New Roman", "font.size": 10})
    fig, axes = plt.subplots(4, 1, figsize=(7.2, 8.8), sharex=True,
                             gridspec_kw={"height_ratios": [3, 3, 3, 1.6]})
    fig.patch.set_facecolor(SURFACE)
    for ax, (ch, label) in zip(axes, PANELS):
        vals = [m["metrics"][ch] for m in msgs]
        q = [m.get("quality", {}).get(ch, []) for m in msgs]
        # линия — только значения без флага spike; выбросы показаны маркерами
        ax.plot(hours, [float("nan") if v is None or "spike" in f else v for v, f in zip(vals, q)],
                color=LINE, lw=0.8, label="значение")
        sp = [(h, v) for h, v, f in zip(hours, vals, q) if "spike" in f]
        st = [(h, v) for h, v, f in zip(hours, vals, q) if "stuck" in f]
        bad = [h for h, f in zip(hours, q) if "nan" in f or "out_of_range" in f]
        lo = min(v for v in vals if v is not None)
        if sp:
            ax.scatter(*zip(*sp), s=28, marker="^", color=SPIKE, edgecolor=SURFACE, lw=1,
                       zorder=3, label=f"spike ({len(sp)})")
        if st:
            ax.scatter(*zip(*st), s=14, marker="s", color=STUCK, edgecolor=SURFACE, lw=0.6,
                       zorder=3, label=f"stuck ({len(st)})")
        if bad:
            ax.scatter(bad, [lo] * len(bad), s=34, marker="x", color=BAD, lw=1.4, zorder=3,
                       label=f"nan / out_of_range → null ({len(bad)})")
        ax.set_ylabel(label, color=INK2)
        ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=8, frameon=False, ncol=4,
                  borderaxespad=0.1, handletextpad=0.3, columnspacing=1.2)
    motion = [m["metrics"]["motion"] for m in msgs]
    per_h = [sum(motion[h * 720:(h + 1) * 720]) for h in range(24)]
    axes[3].bar([h + 0.5 for h in range(24)], per_h, width=0.8, color=LINE)
    axes[3].set_ylabel("Движение,\nопросов/ч", color=INK2)
    axes[3].set_xlabel("Время суток, ч (Астана, UTC+5)", color=INK2)
    axes[3].set_xticks(range(0, 25, 3))
    axes[3].yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True, nbins=4))
    axes[3].set_xlim(0, 24)
    for ax in axes:
        ax.set_facecolor(SURFACE)
        ax.grid(color=GRID, lw=0.6)
        ax.set_axisbelow(True)
        ax.tick_params(colors=INK2, labelsize=9)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(GRID)
    fig.tight_layout()
    fig.savefig(args.out, dpi=200, facecolor=SURFACE)
    flagged = sum(1 for m in msgs if "quality" in m)
    print(f"{args.out}: {n} опросов, с флагами {flagged}, движение за сутки {sum(motion)} опросов")


if __name__ == "__main__":
    main()
