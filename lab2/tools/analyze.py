"""Обработка серий: таблицы (среднее ± 95 % ДИ, процентили) и графики.

    python tools/analyze.py --raw results/raw --img report/img --out results/summary.json
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

sys.path.insert(0, str(Path(__file__).parent))
from channel_model import MCS, PROFILES, rssi_at  # noqa: E402

T95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776}          # t-квантиль, двусторонний 95 %
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
INK2, GRID, SURFACE = "#52514e", "#e6e5e0", "#fcfcfb"
DEADLINE_MS = 1000

# Энергомодель ESP32 (допущения, см. отчёт): ток в активном режиме Wi-Fi и при подключении
I_ACTIVE_MA, V = 120.0, 3.3
T_ASSOC_DHCP_S = 0.8          # ассоциация + DHCP — не эмулируется, типовая оценка
I_SLEEP_MA = 0.01             # deep sleep
I_MODEM_SLEEP_MA = 20.0       # постоянное подключение с modem sleep (DTIM)
BATTERY_MAH = 2500.0          # 2 × AA


def ci(vals: list[float]) -> tuple[float, float]:
    m = st.mean(vals)
    if len(vals) < 2:
        return m, float("nan")
    return m, T95.get(len(vals), 1.96) * st.stdev(vals) / math.sqrt(len(vals))


def pct(v: list[float], p: float) -> float:
    s = sorted(v)
    if not s:
        return float("nan")
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def jitter(rtts: list[float]) -> float:
    """Средний модуль разности соседних RTT (по смыслу RFC 3550)."""
    return st.mean(abs(b - a) for a, b in zip(rtts, rtts[1:])) if len(rtts) > 1 else float("nan")


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
    ap.add_argument("--raw", default="results/raw")
    ap.add_argument("--img", default="report/img")
    ap.add_argument("--out", default="results/summary.json")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    raw, img = Path(args.raw), Path(args.img)
    img.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "Times New Roman", "font.size": 10})

    runs = {p.name: [json.loads(f.read_text(encoding="utf-8"))
                     for f in sorted(raw.glob(f"s*/{p.name}.json"))] for p in PROFILES}
    summary = {}
    for p in PROFILES:
        rs = runs[p.name]
        thr = ci([r["iperf"]["mbps"] for r in rs])
        retr = ci([r["iperf"]["retransmits"] or 0 for r in rs])
        loss = ci([r["ping"]["loss_pct"] for r in rs])
        jit = ci([jitter(r["ping"]["rtt_ms"]) for r in rs])
        rtt = [x for r in rs for x in r["ping"]["rtt_ms"]]
        mq = {}
        for q in (0, 1, 2):
            ms = [m for r in rs for m in r["mqtt"] if m["qos"] == q]
            lat = [x for m in ms for x in m["latency_ms"]]
            sent = sum(m["sent"] for m in ms)
            mq[q] = {"sent": sent, "delivered": sum(m["delivered"] for m in ms),
                     "dups": sum(m["dups"] for m in ms),
                     "in_deadline_pct": 100 * sum(1 for x in lat if x <= DEADLINE_MS) / sent,
                     "p50": pct(lat, 50), "p95": pct(lat, 95), "p99": pct(lat, 99),
                     "mean": st.mean(lat) if lat else float("nan"), "latency": lat}
        cn = {}
        for v in ("plain", "tls12", "tls13"):
            cs = [c for r in rs for c in r["connect"] if c["variant"] == v]
            smp = [x for c in cs for x in c["samples"]]
            tot = [x["tcp"] + x["tls"] + x["mqtt"] for x in smp]
            cn[v] = {"n": sum(c["n"] for c in cs), "fails": sum(c["fails"] for c in cs),
                     **{k: pct([x[k] for x in smp], 50) for k in ("tcp", "tls", "mqtt")},
                     "total_p50": pct(tot, 50), "total_p95": pct(tot, 95)}
        summary[p.name] = {"distance_m": p.distance_m, "rssi": p.rssi, "mcs": p.mcs,
                           "rate_mbps": p.rate_mbps, "series": len(rs),
                           "throughput": thr, "retransmits": retr, "ping_loss": loss,
                           "jitter": jit, "rtt_n": len(rtt), "rtt_p50": pct(rtt, 50),
                           "rtt_p95": pct(rtt, 95), "rtt_p99": pct(rtt, 99),
                           "rtt_mean": st.mean(rtt), "rtt": rtt, "mqtt": mq, "connect": cn}

    # --- печать таблиц ---
    print(f"Throughput / потери / джиттер (среднее ± 95 % ДИ, серий: {summary['d01']['series']})")
    for n, s in summary.items():
        print(f"  {n} RSSI {s['rssi']:6.1f}  iperf {s['throughput'][0]:6.2f} ± {s['throughput'][1]:5.2f} Мбит/с"
              f" ({100 * s['throughput'][0] / s['rate_mbps']:3.0f} % полосы)  retr {s['retransmits'][0]:5.0f}"
              f"  ping loss {s['ping_loss'][0]:5.1f} ± {s['ping_loss'][1]:4.1f} %  jitter {s['jitter'][0]:5.2f} ± {s['jitter'][1]:4.2f} мс")
    print("Задержка, мс (p50 / p95 / p99 / среднее), объединённая выборка")
    for n, s in summary.items():
        print(f"  {n} ping   n={s['rtt_n']:4d}  {s['rtt_p50']:7.1f} {s['rtt_p95']:7.1f} {s['rtt_p99']:7.1f} {s['rtt_mean']:7.1f}")
        for q, m in s["mqtt"].items():
            print(f"  {n} QoS {q}  {m['delivered']}/{m['sent']} dup={m['dups']} ≤1с={m['in_deadline_pct']:5.1f}%"
                  f"  {m['p50']:7.1f} {m['p95']:7.1f} {m['p99']:7.1f} {m['mean']:7.1f}")
    print("Подключение, мс (p50 фаз; итог p50 / p95)")
    for n, s in summary.items():
        for v, c in s["connect"].items():
            print(f"  {n} {v:<6} TCP {c['tcp']:6.1f}  TLS {c['tls']:6.1f}  MQTT {c['mqtt']:6.1f}"
                  f"  итог {c['total_p50']:7.1f} / {c['total_p95']:7.1f}  отказов {c['fails']}/{c['n']}")

    # --- энергобюджет: подключение на каждую отправку против постоянного подключения ---
    energy = {}
    for n, s in summary.items():
        t_conn = T_ASSOC_DHCP_S + s["connect"]["tls13"]["total_p50"] / 1000 + 0.05   # + публикация
        q_conn_mah = I_ACTIVE_MA * t_conn / 3600
        energy[n] = {"t_conn_s": t_conn, "mj": I_ACTIVE_MA * V * t_conn, "mah": q_conn_mah}
    intervals = [10, 60, 300, 900, 3600]
    life = {}
    for iv in intervals:
        row = {}
        for n, e in energy.items():
            per_day = 86400 / iv
            mah_day = e["mah"] * per_day + I_SLEEP_MA * 24
            row[n] = BATTERY_MAH / mah_day
        life[iv] = row
    always_on_days = BATTERY_MAH / (I_MODEM_SLEEP_MA * 24)
    print("Энергия подключения (ассоциация+DHCP 0,8 с + TLS 1.3 + MQTT), 120 мА × 3,3 В")
    for n, e in energy.items():
        print(f"  {n}: {e['t_conn_s']:.2f} с  {e['mj']:.0f} мДж  {e['mah'] * 1000:.1f} мкА·ч")
    print(f"Автономность 2500 мА·ч, сутки (подключение на каждую отправку); постоянное: {always_on_days:.1f}")
    for iv, row in life.items():
        print(f"  {iv:>5} с  " + "  ".join(f"{n} {d:7.1f}" for n, d in row.items()))

    out = {n: {k: v for k, v in s.items() if k != "rtt"} for n, s in summary.items()}
    for s in out.values():
        s["mqtt"] = {q: {k: v for k, v in m.items() if k != "latency"} for q, m in s["mqtt"].items()}
    out["_energy"] = {"per_connect": energy, "life_days": life, "always_on_days": always_on_days}
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    names = list(summary)
    rssi = [summary[n]["rssi"] for n in names]

    # 1. RSSI(d)
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    d = [x / 10 for x in range(5, 351)]
    ax.plot(d, [rssi_at(x) for x in d], color=SERIES[0], lw=1.6, label="модель: n = 3,3")
    ax.scatter([summary[n]["distance_m"] for n in names], rssi, s=42, color=SERIES[0],
               edgecolor=SURFACE, lw=1.5, zorder=3)
    for n in names:
        below = n == "d30"
        ax.annotate(f"{summary[n]['rssi']:.1f} дБм".replace(".", ","),
                    (summary[n]["distance_m"], summary[n]["rssi"]), textcoords="offset points",
                    xytext=(6, -12 if below else 6), fontsize=8, color=INK2)
    for i in (0, 7):
        ax.axhline(MCS[i][0], color=INK2, lw=0.8, ls="--")
        ax.text(1, MCS[i][0] + 1, f"чувствительность MCS{i}: {MCS[i][0]} дБм", ha="left",
                fontsize=8, color=INK2)
    ax.set_xlabel("Расстояние до точки доступа, м", color=INK2)
    ax.set_ylabel("RSSI, дБм", color=INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper right", bbox_to_anchor=(1, 0.88))
    style(ax); fig.tight_layout(); fig.savefig(img / "fig_rssi.png", dpi=200, facecolor=SURFACE); plt.close(fig)

    # 2. throughput(RSSI)
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    thr = [summary[n]["throughput"] for n in names]
    ax.plot(rssi, [summary[n]["rate_mbps"] for n in names], color=INK2, lw=1, ls="--", marker="o", ms=4,
            label="ёмкость канала (PHY × КПД MAC)")
    ax.errorbar(rssi, [t[0] for t in thr], yerr=[t[1] for t in thr], color=SERIES[0], lw=1.8,
                marker="o", ms=6, capsize=4, label="TCP throughput, iperf3 (± 95 % ДИ)")
    for x, t in zip(rssi, thr):
        ax.annotate(f"{t[0]:.2f}".replace(".", ","), (x, t[0]), textcoords="offset points",
                    xytext=(0, -16), ha="center", fontsize=8, color=INK2)
    ax.set_yscale("log")
    ax.invert_xaxis()
    ax.set_xlabel("RSSI, дБм", color=INK2)
    ax.set_ylabel("Мбит/с (логарифмическая шкала)", color=INK2)
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    style(ax); fig.tight_layout(); fig.savefig(img / "fig_throughput.png", dpi=200, facecolor=SURFACE); plt.close(fig)

    # 3. loss(RSSI)
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    pl = [summary[n]["ping_loss"] for n in names]
    ax.errorbar(rssi, [x[0] for x in pl], yerr=[x[1] for x in pl], color=SERIES[0], lw=1.8, marker="o",
                ms=6, capsize=4, label="потери ping (± 95 % ДИ)")
    for i, q in enumerate((0, 1, 2)):
        ax.plot(rssi, [100 - summary[n]["mqtt"][q]["in_deadline_pct"] for n in names],
                color=SERIES[i + 1], lw=1.6, marker="s", ms=5, label=f"MQTT QoS {q}: не доставлено за 1 с")
    ax.invert_xaxis()
    ax.set_xlabel("RSSI, дБм", color=INK2)
    ax.set_ylabel("%", color=INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    style(ax); fig.tight_layout(); fig.savefig(img / "fig_loss.png", dpi=200, facecolor=SURFACE); plt.close(fig)

    # 4. latency CDF: ping RTT
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    for i, n in enumerate(names):
        r = sorted(summary[n]["rtt"])
        ax.plot(r, [(k + 1) / len(r) for k in range(len(r))], color=SERIES[i], lw=1.8,
                label=f"{summary[n]['rssi']:.0f} дБм ({summary[n]['distance_m']:g} м)")
    ax.axhline(0.99, color=INK2, lw=0.8, ls=":")
    ax.text(0.55, 0.965, "p99", fontsize=8, color=INK2)
    ax.set_xscale("log")
    ax.set_xlim(left=0.5)        # netem обрезает отрицательные задержки нормального распределения до 0
    ax.set_xlabel("RTT ping, мс (логарифмическая шкала)", color=INK2)
    ax.set_ylabel("Доля пакетов", color=INK2)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    style(ax); fig.tight_layout(); fig.savefig(img / "fig_cdf_ping.png", dpi=200, facecolor=SURFACE); plt.close(fig)

    # 5. latency CDF: MQTT QoS 0/1/2 на трёх профилях
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 3.0), sharey=True)
    for ax, n in zip(axes, ("d10", "d20", "d30")):
        for i, q in enumerate((0, 1, 2)):
            lat = sorted(summary[n]["mqtt"][q]["latency"])
            ax.plot(lat, [(k + 1) / summary[n]["mqtt"][q]["sent"] for k in range(len(lat))],
                    color=SERIES[i], lw=1.6, label=f"QoS {q}")
        ax.set_xscale("log")
        ax.set_title(f"{summary[n]['rssi']:.0f} дБм ({summary[n]['distance_m']:g} м)", fontsize=9, color=INK2)
        ax.set_xlabel("задержка, мс", color=INK2)
        style(ax)
    axes[0].set_ylabel("Доля сообщений", color=INK2)
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout(); fig.savefig(img / "fig_cdf_mqtt.png", dpi=200, facecolor=SURFACE); plt.close(fig)

    # 6. время подключения по фазам (TLS 1.3)
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    x = range(len(names))
    bottom = [0.0] * len(names)
    for i, (k, lab) in enumerate((("tcp", "TCP handshake"), ("tls", "TLS 1.3"), ("mqtt", "MQTT CONNECT/CONNACK"))):
        vals = [summary[n]["connect"]["tls13"][k] for n in names]
        ax.bar(x, vals, bottom=bottom, color=SERIES[i], width=0.6, label=lab, edgecolor=SURFACE, lw=1)
        bottom = [b + v for b, v in zip(bottom, vals)]
    for i, n in enumerate(names):
        ax.text(i, bottom[i] * 1.02, f"{bottom[i]:.0f}", ha="center", fontsize=8, color=INK2)
    ax.set_xticks(list(x), [f"{summary[n]['rssi']:.0f} дБм\n{summary[n]['distance_m']:g} м" for n in names])
    ax.set_ylabel("мс (сумма медиан фаз)", color=INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    style(ax); fig.tight_layout(); fig.savefig(img / "fig_connect.png", dpi=200, facecolor=SURFACE); plt.close(fig)
    print("графики:", ", ".join(sorted(p.name for p in img.glob("fig_*.png"))))


if __name__ == "__main__":
    main()
