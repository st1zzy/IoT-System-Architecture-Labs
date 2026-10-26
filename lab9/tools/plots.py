"""Графики Lab 9: стоимость подключения (время по фазам, байты) и хронология отзыва сертификата.

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
NAMES = ["plain", "TLS 1.2 mTLS", "TLS 1.3 mTLS", "TLS 1.3 mTLS + resumption"]
SHORT = ["без TLS", "TLS 1.2\nmTLS", "TLS 1.3\nmTLS", "TLS 1.3 mTLS\n+ resumption"]


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
    img.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "Times New Roman", "font.size": 10})
    cost = json.loads((RES / "cost.json").read_text(encoding="utf-8"))["connect"]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.2), gridspec_kw={"width_ratios": [1.4, 1]})
    w = 0.38
    for j, delay in enumerate((0, 40)):
        xs = [i + (j - 0.5) * w for i in range(4)]
        bottom = [0.0] * 4
        for k, (ph, lab) in enumerate((("tcp_ms", "TCP"), ("tls_ms", "TLS"), ("mqtt_ms", "MQTT CONNECT"))):
            v = [cost[f"{n} @RTT+{delay}"][ph] for n in NAMES]
            a1.bar(xs, v, w * 0.95, bottom=bottom, color=SER[k], alpha=1 if delay else 0.45,
                   label=lab if j == 1 else None, edgecolor=SURFACE, lw=0.6)
            bottom = [b + x for b, x in zip(bottom, v)]
        for x, b in zip(xs, bottom):
            a1.text(x, b + 3, f"{b:.0f}", ha="center", fontsize=7, color=INK2)
    a1.set_xticks(range(4), SHORT, fontsize=8)
    a1.set_ylabel("Подключение, мс (медиана из 30)", color=INK2)
    a1.set_title("светлые — RTT 0, тёмные — RTT 40 мс", fontsize=8, color=INK2)
    a1.legend(frameon=False, fontsize=8, loc="upper left")
    style(a1)
    b = [cost[f"{n} @RTT+0"]["bytes"] / 1024 for n in NAMES]
    bars = a2.bar(range(4), b, 0.6, color=[INK2, SER[1], SER[0], SER[2]])
    for bar, v in zip(bars, b):
        a2.text(bar.get_x() + bar.get_width() / 2, v + 0.15, f"{v:.1f}".replace(".", ","), ha="center", fontsize=8, color=INK2)
    a2.set_xticks(range(4), SHORT, fontsize=8)
    a2.set_ylabel("КБ на подключение (оба направления)", color=INK2)
    style(a2)
    fig.tight_layout()
    fig.savefig(img / "fig_cost.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)

    rv = json.loads((RES / "revocation.json").read_text(encoding="utf-8"))
    ev = rv["events"]
    t0 = ev["decision"]
    probe = [json.loads(x) for x in (RES / "probe.jsonl").read_text().splitlines()]
    hold = [json.loads(x) for x in (RES / "hold.jsonl").read_text().splitlines()]
    fig, ax = plt.subplots(figsize=(7.2, 2.8))
    ok = [p["t"] - t0 for p in probe if p["ok"]]
    bad = [p["t"] - t0 for p in probe if not p["ok"]]
    ax.scatter(ok, [2] * len(ok), s=10, color=SER[2], label="новое подключение принято")
    ax.scatter(bad, [2] * len(bad), s=10, color=SER[1], marker="x", label="новое подключение отклонено (revoked)")
    pa = [h["t"] - t0 for h in hold if h["ev"] == "puback"]
    ax.scatter(pa, [1] * len(pa), s=10, color=SER[0], label="открытый сеанс: PUBACK")
    for t, lab, y in ((0, "решение\n+ CRL", 2.45), (ev["sighup"] - t0, "SIGHUP", 1.6),
                      (ev["restart"] - t0, "перезапуск\nброкера", 2.45)):
        ax.axvline(t, color=INK2, lw=0.8, ls="--")
        ax.text(t + 0.3, y, lab, fontsize=8, color=INK2, va="top")
    ax.set_yticks([1, 2], ["сеанс, открытый\nдо отзыва", "новые\nподключения"])
    ax.set_ylim(0.5, 2.6)
    ax.set_xlim(-15, 60)
    ax.set_xlabel("Секунды от решения об отзыве dev-003", color=INK2)
    ax.legend(frameon=False, fontsize=7, loc="lower right", ncol=1)
    style(ax)
    fig.tight_layout()
    fig.savefig(img / "fig_revocation.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)
    print("графики: fig_cost.png, fig_revocation.png")


if __name__ == "__main__":
    main()
