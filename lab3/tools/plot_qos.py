"""Графики Lab 3: CDF задержки QoS 0/1/2 при потерях 0/5/20 % и доставка при окне 20 и 50.

    python tools/plot_qos.py --img report/img
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402

RES = Path(__file__).resolve().parent.parent / "results"
SER = ["#2a78d6", "#eb6834", "#1baf7a"]
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default="report/img")
    img = Path(ap.parse_args().img)
    img.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "Times New Roman", "font.size": 10})

    fig, axes = plt.subplots(1, 3, figsize=(7.4, 3.1), sharey=True)
    for ax, loss in zip(axes, (0, 5, 20)):
        for i, q in enumerate((0, 1, 2)):
            lat, n = [], 0
            for s in (1, 2, 3):
                d = json.loads((RES / "qos" / f"q{q}_l{loss}_s{s}_sink.json").read_text())
                lat += d["latency_ms"]
                n += d["n"]
            lat.sort()
            ax.plot(lat, [(k + 1) / n for k in range(len(lat))], color=SER[i], lw=1.6, label=f"QoS {q}")
        ax.set_xscale("log")
        ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        ax.set_title(f"потери {loss} % в каждую сторону", fontsize=9, color=INK2)
        ax.set_xlabel("задержка, мс", color=INK2)
        style(ax)
    axes[0].set_ylabel("Доля сообщений (от отправленных)", color=INK2)
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(img / "fig_qos_cdf.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)

    w20 = json.loads((RES / "qos_summary.json").read_text())
    w50 = json.loads((RES / "qos_inflight50_summary.json").read_text())
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    labels, x = [], 0
    for loss in (0, 5, 20):
        for q in (0, 1, 2):
            a = next(r for r in w20 if r["qos"] == q and r["loss"] == loss)
            b = next(r for r in w50 if r["qos"] == q and r["loss"] == loss)
            ax.bar(x - 0.2, a["delivered_pct"], 0.38, color=SER[0], label="окно 20 = лимиту брокера" if x == 0 else None)
            ax.bar(x + 0.2, b["delivered_pct"], 0.38, color=SER[1], label="окно 50 > лимита брокера" if x == 0 else None)
            labels.append(f"QoS {q}\n{loss} %")
            x += 1
    ax.set_xticks(range(len(labels)), labels, fontsize=8)
    ax.set_ylim(50, 102)
    ax.set_ylabel("Доставлено, %", color=INK2)
    ax.legend(frameon=False, fontsize=8, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2)
    style(ax)
    fig.tight_layout()
    fig.savefig(img / "fig_qos_window.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)
    lw = json.loads((RES / "lwt" / "lwt.json").read_text())
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    ks = sorted({r["keepalive"] for r in lw})
    for j, (mode, lab, c) in enumerate((("kill", "kill -9 (ОС закрывает сокет)", SER[0]),
                                         ("silent", "тихий отказ (сеть пропала)", SER[1]))):
        for i, k in enumerate(ks):
            v = [r["detect_s"] for r in lw if r["keepalive"] == k and r["mode"] == mode]
            m = sum(v) / len(v)
            ax.bar(i + (j - 0.5) * 0.36, m, 0.34, color=c, label=lab if i == 0 else None)
            ax.scatter([i + (j - 0.5) * 0.36] * len(v), v, color="#0b0b0b", s=8, zorder=3)
            ax.text(i + (j - 0.5) * 0.36, max(v) + 0.8, f"{m:.1f}".replace(".", ",") if m >= 0.1 else "0,002",
                    ha="center", fontsize=8, color=INK2)
    for i, k in enumerate(ks):
        ax.plot([i - 0.45, i + 0.45], [1.5 * k] * 2, color=INK2, lw=0.8, ls="--")
    ax.text(-0.45, 1.5 * ks[0] + 0.8, "1,5 × keep-alive", fontsize=8, color=INK2, ha="left")
    ax.set_xticks(range(len(ks)), [f"keep-alive {k} с" for k in ks])
    ax.set_ylabel("Время до LWT offline, с", color=INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    style(ax)
    fig.tight_layout()
    fig.savefig(img / "fig_lwt.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)
    print("графики: fig_qos_cdf.png, fig_qos_window.png, fig_lwt.png")


if __name__ == "__main__":
    main()
