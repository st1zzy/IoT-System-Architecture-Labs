"""Графики Lab S1 по results/*.json.

    python tools/plots.py --img report/img
"""

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from lorawan import lifetime_years, min_period_s  # noqa: E402

RES = Path(__file__).resolve().parent.parent / "results"
SER = {7: "#2a78d6", 9: "#eb6834", 12: "#1baf7a"}
C4, C5, C6 = "#eda100", "#e87ba4", "#6250d6"
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


def save(fig, path) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default="report/img")
    img = Path(ap.parse_args().img)
    img.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "Times New Roman", "font.size": 10})
    load = lambda n: json.loads((RES / n).read_text(encoding="utf-8"))  # noqa: E731

    # 1. проверка на пределе ALOHA
    al = load("aloha.json")
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    g = [x / 100 for x in range(1, 301)]
    ax.plot(g, [x * math.exp(-2 * x) for x in g], color=INK2, lw=1.2, ls="--", label="теория: S = G·e^(−2G)")
    ax.scatter([a["G"] for a in al], [a["S_sim"] for a in al], s=36, color=SER[7], edgecolor=SURFACE,
               lw=1.2, zorder=3, label="симуляция (3 повтора)")
    ax.annotate("максимум 1/(2e) = 0,184\nпри G = 0,5", (0.5, 0.1839), textcoords="offset points",
                xytext=(30, -8), fontsize=8, color=INK2, arrowprops={"arrowstyle": "-", "color": INK2, "lw": 0.6})
    ax.set_xlabel("Нагрузка G, передач за время одного пакета", color=INK2)
    ax.set_ylabel("Пропускная способность S", color=INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    style(ax); save(fig, img / "fig_aloha.png")

    # 2. PDR(N) для SF7/9/12 при трёх интервалах
    gr = load("grid.json")
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 3.2), sharey=True)
    for ax, per in zip(axes, (300, 600, 3600)):
        for sf in (7, 9, 12):
            for cap, ls in ((True, "-"), (False, "--")):
                pts = sorted((r["n"], r["pdr"]) for r in gr if r["sf"] == sf and r["period"] == per
                             and r["capture"] == cap)
                ax.plot(*zip(*pts), color=SER[sf], lw=1.6 if cap else 1.0, ls=ls, marker="o" if cap else None,
                        ms=3.5, label=f"SF{sf}" if cap else None)
        ax.axhline(0.95, color=INK2, lw=0.7, ls=":")
        ax.set_xscale("log")
        ax.set_title({300: "1 раз в 5 мин", 600: "1 раз в 10 мин", 3600: "1 раз в час"}[per], fontsize=9, color=INK2)
        ax.set_xlabel("Устройств на шлюз", color=INK2)
        style(ax)
    axes[0].set_ylabel("PDR", color=INK2)
    axes[2].legend(frameon=False, fontsize=8, loc="lower left", title="сплошная — с захватом,\nпунктир — без",
                   title_fontsize=7)
    save(fig, img / "fig_pdr.png")

    # 3. причины потерь для SF12, 1 раз в 10 мин, с захватом
    rows = sorted((r for r in gr if r["sf"] == 12 and r["period"] == 600 and r["capture"]), key=lambda r: r["n"])
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    x = range(len(rows))
    parts = [("доставлено", [r["pdr"] for r in rows], SER[7]),
             ("коллизии", [r["collided"] for r in rows], SER[9]),
             ("нет свободного демодулятора", [r["demod"] for r in rows], C6)]
    bottom = [0.0] * len(rows)
    for lab, v, c in parts:
        ax.bar(x, v, bottom=bottom, color=c, width=0.65, label=lab, edgecolor=SURFACE, lw=1)
        bottom = [b + y for b, y in zip(bottom, v)]
    ax.set_xticks(list(x), [str(r["n"]) for r in rows])
    ax.set_xlabel("Устройств на шлюз (SF12, 1 раз в 10 мин)", color=INK2)
    ax.set_ylabel("Доля передач", color=INK2)
    ax.legend(frameon=False, fontsize=8, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3)
    style(ax); save(fig, img / "fig_breakdown.png")

    # 4. ADR против «все на SF12»
    ad = load("adr.json")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.2), gridspec_kw={"width_ratios": [1, 1.2]})
    w = 0.38
    for j, (rad, c) in enumerate(((3.0, SER[7]), (5.0, SER[9]))):
        share = next(r for r in ad if r["mode"] == "adr" and r["n"] == 1000 and r["radius_km"] == rad)["sf_share"]
        xs = [i + (j - 0.5) * w for i in range(6)]
        a1.bar(xs, [100 * share[str(k)] for k in range(7, 13)], width=w, color=c, label=f"R = {rad:g} км",
               edgecolor=SURFACE, lw=1)
    a1.set_xticks(range(6), [f"SF{k}" for k in range(7, 13)])
    a1.set_ylabel("Доля устройств, %", color=INK2)
    a1.set_title("Распределение SF при ADR", fontsize=9, color=INK2)
    a1.legend(frameon=False, fontsize=8, loc="upper left")
    style(a1)
    for rad, ls in ((3.0, "-"), (5.0, "--")):
        for mode, lab, c in (("adr", "ADR", SER[7]), ("sf12", "все на SF12", SER[12])):
            pts = sorted((r["n"], r["pdr"]) for r in ad if r["mode"] == mode and r["radius_km"] == rad)
            a2.plot(*zip(*pts), color=c, lw=1.8 if ls == "-" else 1.2, ls=ls, marker="o", ms=4,
                    label=f"{lab}, R = {rad:g} км")
    a2.set_xscale("log")
    a2.set_xticks([500, 1000, 2000, 5000], ["500", "1000", "2000", "5000"])
    a2.minorticks_off()
    a2.set_xlabel("Устройств на шлюз (1 раз в 10 мин)", color=INK2)
    a2.set_ylabel("PDR", color=INK2)
    a2.legend(frameon=False, fontsize=7, loc="lower left")
    style(a2); save(fig, img / "fig_adr.png")

    # 5. энергия сообщения по фазам
    en = load("energy.json")
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    colors = [C4, C5, SER[9], SER[7], C6, INK2]
    for row, sf in enumerate(("7", "9", "12")):
        left = 0.0
        for k, ph in enumerate(en["phases"][sf]):
            ax.barh(row, ph["uah"], left=left, color=colors[k], height=0.55, edgecolor=SURFACE, lw=1,
                    label=ph["phase"] if row == 0 else None)
            left += ph["uah"]
        ax.text(left + 0.2, row, f"{left:.2f} мкА·ч", va="center", fontsize=8, color=INK2)
    ax.set_yticks([0, 1, 2], ["SF7", "SF9", "SF12"])
    ax.invert_yaxis()
    ax.set_xlabel("Заряд на одно сообщение, мкА·ч", color=INK2)
    ax.set_xlim(0, 22)
    ax.legend(frameon=False, fontsize=7, loc="upper left", bbox_to_anchor=(0, -0.22), ncol=3)
    style(ax); save(fig, img / "fig_energy.png")

    # 6. срок службы от частоты передачи
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    rates = [10 ** (x / 20) for x in range(-30, 61)]          # от 1/30 до 1000 в час
    for sf in (7, 9, 12):
        ok = [r for r in rates if 3600 / r >= min_period_s(sf)]
        ax.plot(ok, [lifetime_years(sf, r) for r in ok], color=SER[sf], lw=1.8, label=f"SF{sf}")
        ax.scatter([ok[-1]], [lifetime_years(sf, ok[-1])], color=SER[sf], s=24, zorder=3)
    ax.axhline(10, color=INK2, lw=0.8, ls="--")
    ax.text(0.04, 11.5, "срок годности батареи ≈ 10 лет", fontsize=8, color=INK2)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("Сообщений в час (точка — предел duty cycle 1 %)", color=INK2)
    ax.set_ylabel("Срок службы, лет", color=INK2)
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    style(ax); save(fig, img / "fig_lifetime.png")
    print("графики:", ", ".join(sorted(p.name for p in img.glob("fig_*.png"))))


if __name__ == "__main__":
    main()
