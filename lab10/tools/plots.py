"""Графики Lab 10 и сводка results/summary.json.

    uv run --no-project --with matplotlib python tools/plots.py --img report/img
"""
import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RES = Path(__file__).resolve().parent.parent / "results"
RUNS = RES / "runs"
SER = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#8a5cd6"]
INK2, GRID, SURFACE, RED = "#52514e", "#e6e5e0", "#fcfcfb", "#d03b3b"
LOAD_RUNS = ["single-1k", "single-5k", "single-10k", "cluster-10k", "kafka-10k-c2", "tuned-10k-c2", "tuned-10k-c2b",
             "three-10k-c2b",
             "cluster-20k", "tuned-20k-c2", "three-20k-c2"]
ALL_RUNS = LOAD_RUNS + ["kafka-10k-c1", "tuned-10k-c2", "three-10k-c2", "failover-nobuffer", "failover-buffer",
                        "herd-immediate", "herd-exp", "herd-exp_jitter", "herd-exp_jitter10"]
LABEL = {k: v.replace("|", "\n") for k, v in {
    "single-1k": "1 узел|1k", "single-5k": "1 узел|5k", "single-10k": "1 узел|10k",
    "cluster-10k": "2 узла|10k", "kafka-10k-c2": "2 узла|+Kafka|10k", "tuned-10k-c2": "2 узла|+Kafka|+лимиты|10k, №1", "tuned-10k-c2b": "2 узла|+Kafka|+лимиты|10k, №2",
    "three-10k-c2b": "3 узла|+Kafka|+лимиты|10k", "cluster-20k": "2 узла|20k",
    "tuned-20k-c2": "2 узла|+Kafka|+лимиты|20k", "three-20k-c2": "3 узла|+Kafka|+лимиты|20k"}.items()}
NFR_P99 = 1000          # мс


def num(x):
    """Число с пробелом-разделителем тысяч (как в тексте отчёта)."""
    return f"{x:,.0f}".replace(",", " ")


def style(ax, grid="y"):
    ax.set_facecolor(SURFACE)
    ax.grid(color=GRID, lw=0.6, axis=grid)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK2, labelsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)


def load(name):
    p = RUNS / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def cpu_series(r, name):
    return [(row["t"], row["cpu"].get(name)) for row in r["samples"] if row["cpu"].get(name) is not None]


def summary():
    out = {}
    for name in ALL_RUNS:
        r = load(name)
        if not r:
            continue
        c = r["loadgen"]["counters"]
        d = r["emqx_delta"]
        lags = [row.get("lag") for row in r["samples"] if row.get("lag") is not None]
        out[name] = dict(
            clients=r["clients"], rate=r["rate"], offered=r["offered"], generated=c["generated"], acked=c["acked"],
            stored=r["stored_distinct"], duplicates=r["duplicates"], not_stored=r["not_stored"],
            lost_after_ack=r["lost_after_ack"], tput_steady=r["tput_steady"],
            p50=r["lat_p50"], p95=r["lat_p95"], p99=r["lat_p99"], lat_max=r["lat_max"],
            conn_ok=c["conn_ok"], conn_fail=c["conn_fail"], conn_attempts=c["conn_attempts"],
            disconnects=c["disconnects"], devices_disconnected=c["devices_disconnected"],
            reconnects=c["reconnects"], dropped_offline=c["dropped_offline"], lost_inflight=c["lost_inflight"],
            resent=c.get("resent", 0), dropped_window=c.get("dropped_window", 0),
            connack=r["loadgen"]["connack_ms"], reconnect_s=r["loadgen"]["reconnect_s"],
            peak_attempts_per_100ms=r["loadgen"]["peak_attempts_per_bin"],
            queue_full=d.get("delivery.dropped.queue_full", 0),
            no_subscribers=d.get("messages.dropped.no_subscribers", 0),
            emqx_received=d.get("messages.received", 0), emqx_delivered=d.get("messages.delivered", 0),
            cpu_steady=r.get("cpu_steady"), cpu_peak=r.get("cpu_peak"), fds=r["fds"],
            lag_max=max(lags) if lags else None, event=r["event"], steady_lat=r.get("steady_lat"),
            start_pub=r["loadgen"]["start_pub"], end_t=r["loadgen"]["end_t"], t0=r["loadgen"]["t0"],
            recover=_recover(r),
        )
    d = RUNS / "drain.json"
    if d.exists():
        dr = json.loads(d.read_text())
        out["drain"] = [dict(n=x["n"], backlog=x["backlog"], rate=x["rate"], elapsed=x["elapsed"],
                             startup=x.get("startup"), distinct=x.get("distinct"), cpu=x.get("cpu")) for x in dr]
    (RES / "summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _recover(r):
    """После события (kill узла / перезапуск NGINX): минимум подключённых, время до 90/99 % клиентов,
    пиковое число попыток CONNECT в секунду (бины 0,1 с) и всего попыток после события."""
    if not r.get("event"):
        return None
    lg = r["loadgen"]
    b = lg["series"]["bin"]
    conn = {int(k): v for k, v in lg["series"]["connected"].items()}
    att = {int(k): v for k, v in lg["series"]["attempts"].items()}
    fails = {int(k): v for k, v in lg["series"]["fails"].items()}
    ev = int((r["event"]["t"] - lg["t0"]) / b)
    end = int((lg["end_t"] - lg["t0"]) / b)
    after = [k for k in sorted(conn) if ev <= k <= end]
    low = min(conn[k] for k in after)
    kmin = min(after, key=lambda k: conn[k])
    out = dict(min_connected=low, peak_attempts_per_s=max(att.get(k, 0) for k in after) / b,
               attempts_after=sum(v for k, v in att.items() if k >= ev),
               fails_after=sum(v for k, v in fails.items() if k >= ev))
    for q in (0.9, 0.99):
        hit = next((k for k in after if k >= kmin and conn[k] >= q * r["clients"]), None)
        out[f"t{int(q * 100)}_s"] = None if hit is None else round((hit - ev) * b, 1)
    return out


def _ingest_cpu(x):
    vals = {}
    for row in x["samples"]:
        for k, v in row["cpu"].items():
            if k.startswith("ingest-") and v > 1:
                vals.setdefault(k, []).append(v)
    return {k: sum(v) / len(v) for k, v in vals.items()}


def fig_runs(S, img):
    names = [n for n in LOAD_RUNS if n in S]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.6, 5.6), sharex=True)
    x = range(len(names))
    off = [S[n]["offered"] for n in names]
    got = [S[n]["tput_steady"] for n in names]
    a1.bar([i - 0.2 for i in x], off, 0.38, color=GRID, edgecolor=INK2, lw=0.5, label="предложено, сообщ./с")
    a1.bar([i + 0.2 for i in x], got, 0.38, color=SER[0], label="записано в БД, сообщ./с (уст. режим)")
    for i, n in enumerate(names):
        loss = S[n]["not_stored"] / max(1, S[n]["generated"]) * 100
        a1.text(i + 0.2, got[i] + 150, f"потери\n{loss:.1f} %" if loss >= 0.05 else "0 %", ha="center", fontsize=6.5,
                color=RED if loss >= 0.05 else INK2)
    a1.set_ylabel("сообщений/с", color=INK2)
    a1.legend(frameon=False, fontsize=7.5, loc="upper left")
    style(a1)
    p99 = [S[n]["p99"] or 0 for n in names]
    a2.bar(x, p99, 0.55, color=[SER[2] if v <= NFR_P99 else RED for v in p99])
    a2.axhline(NFR_P99, color=INK2, lw=0.8, ls="--")
    a2.text(len(names) - 0.5, NFR_P99 * 1.15, "NFR: p99 ≤ 1 с", ha="right", fontsize=7.5, color=INK2)
    a2.set_yscale("log")
    a2.set_ylabel("p99 устройство → БД, мс", color=INK2)
    for i, v in enumerate(p99):
        a2.text(i, v * 1.12, num(v), ha="center", fontsize=7, color=INK2)
    a2.set_xticks(list(x), [LABEL[n] for n in names], fontsize=6.5)
    style(a2)
    fig.tight_layout()
    fig.savefig(img / "fig_runs.png", dpi=200)
    plt.close(fig)


def fig_single(img):
    """Хронология прогона single-10k: CPU брокера, запись в БД, p99."""
    rows = [(n, load(n)) for n in ("single-5k", "single-10k")]
    rows = [(n, r) for n, r in rows if r]
    if not rows:
        return
    fig, axes = plt.subplots(len(rows), 1, figsize=(7.4, 2.6 * len(rows)), squeeze=False)
    for ax, (name, r) in zip(axes[:, 0], rows):
        t0 = r["loadgen"]["t0"]
        ts = [(t - t0, n) for t, n in r["tput_series"]]
        ax.plot([a for a, _ in ts], [b for _, b in ts], color=SER[0], lw=1.2, label="записано в БД, строк/с")
        ax.axhline(r["offered"], color=SER[0], lw=0.8, ls=":", label="предложено")
        ax.set_ylabel("сообщ./с", color=INK2)
        ax2 = ax.twinx()
        cs = cpu_series(r, "emqx1")
        ax2.plot([t - t0 for t, _ in cs], [v for _, v in cs], color=SER[1], lw=1.2, label="CPU emqx1, %")
        ax2.set_ylim(0, 130)
        ax2.axhline(100, color=SER[1], lw=0.6, ls="--")
        ax2.set_ylabel("CPU, % (квота 100)", color=INK2)
        ax2.tick_params(colors=INK2, labelsize=8)
        for s in ("top",):
            ax2.spines[s].set_visible(False)
        ax.axvline(r["loadgen"]["start_pub"] - t0, color=INK2, lw=0.6)
        ax.set_title(f"{name}: {num(r['clients'])} клиентов × {str(r['rate']).replace('.', ',')} сообщ./с, "
                     f"p99 = {num(r['lat_p99'])} мс", fontsize=9, color=INK2)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, frameon=False, fontsize=7, loc="upper left", ncol=3)
        style(ax)
    axes[-1, 0].set_xlabel("время от старта, с (вертикаль — конец разгона подключений)", color=INK2)
    fig.tight_layout()
    fig.savefig(img / "fig_single.png", dpi=200)
    plt.close(fig)


def fig_scaling(S, img):
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(7.6, 2.9))
    if "drain" in S:
        dr = S["drain"]
        ns = [d["n"] for d in dr]
        base = dr[0]["rate"]
        a1.plot(ns, [base * n / 1000 for n in ns], color=INK2, ls="--", lw=0.8, marker="o", ms=3, label="линейно")
        a1.plot(ns, [d["rate"] / 1000 for d in dr], color=SER[0], marker="o", lw=1.6, label="измерено")
        for d in dr:
            a1.text(d["n"], d["rate"] / 1000 - 12, f"×{d['rate'] / base:.2f}", ha="center", va="top", fontsize=7, color=INK2)
        a1.set_xticks(ns)
        a1.set_ylim(0, base * 4.4 / 1000)
        a1.set_xlabel("consumer'ов в группе", color=INK2)
        a1.set_ylabel("тыс. сообщ./с", color=INK2)
        a1.set_title("а) разбор очереди 3 млн", fontsize=8.5, color=INK2)
        a1.legend(frameon=False, fontsize=7)
        style(a1)
    # все повторы — точками, линия — худший из повторов
    sets = {"10k (2000 сообщ./с)": [["single-10k"], ["tuned-10k-c2", "tuned-10k-c2b"], ["three-10k-c2", "three-10k-c2b"]],
            "20k (4000 сообщ./с)": [[], ["tuned-20k-c2"], ["three-20k-c2"]]}
    for k, (lab, groups) in enumerate(sets.items()):
        xs, dv, p9 = [], [], []
        for nodes, names in zip((1, 2, 3), groups):
            names = [n for n in names if n in S]
            if not names:
                continue
            d = [S[n]["stored"] / max(1, S[n]["generated"]) * 100 for n in names]
            p = [S[n]["p99"] for n in names]
            a2.scatter([nodes] * len(d), d, color=SER[k], s=14, zorder=3)
            a3.scatter([nodes] * len(p), p, color=SER[k], s=14, zorder=3)
            xs.append(nodes)
            dv.append(min(d))
            p9.append(max(p))
        a2.plot(xs, dv, color=SER[k], lw=1.5, label=lab)
        a3.plot(xs, p9, color=SER[k], lw=1.5, label=lab)
    a2.set_xticks([1, 2, 3])
    a2.set_ylim(0, 105)
    a2.set_xlabel("узлов EMQX (по 1 vCPU)", color=INK2)
    a2.set_ylabel("записано в БД, %", color=INK2)
    a2.set_title("б) доставка", fontsize=8.5, color=INK2)
    a2.legend(frameon=False, fontsize=6.5, loc="lower right")
    style(a2)
    a3.set_yscale("log")
    a3.axhline(NFR_P99, color=INK2, lw=0.8, ls="--")
    a3.text(3, NFR_P99 * 1.15, "NFR", ha="right", fontsize=7, color=INK2)
    a3.set_xticks([1, 2, 3])
    a3.set_xlabel("узлов EMQX (по 1 vCPU)", color=INK2)
    a3.set_ylabel("p99 устройство → БД, мс", color=INK2)
    a3.set_title("в) задержка (линия — худший повтор)", fontsize=8.5, color=INK2)
    style(a3)
    fig.tight_layout()
    fig.savefig(img / "fig_scaling.png", dpi=200)
    plt.close(fig)


def bins(r, key):
    s = r["loadgen"]["series"]
    b = s["bin"]
    d = {int(k): v for k, v in s[key].items()}
    return b, d


def fig_failover(img):
    rows = [(n, load(n)) for n in ("failover-nobuffer", "failover-buffer")]
    rows = [(n, r) for n, r in rows if r]
    if not rows:
        return
    fig, axes = plt.subplots(len(rows), 1, figsize=(7.4, 2.5 * len(rows)), squeeze=False, sharex=True)
    for ax, (name, r) in zip(axes[:, 0], rows):
        lg = r["loadgen"]
        b, conn = bins(r, "connected")
        _, acks = bins(r, "acks")
        kill = r["event"]["t"] - lg["t0"]
        xs = sorted(conn)
        ax.plot([k * b for k in xs], [conn[k] for k in xs], color=SER[0], lw=1.3, label="подключено клиентов")
        ax.set_ylabel("клиентов", color=INK2)
        ax2 = ax.twinx()
        sec = {}
        for k, v in acks.items():
            sec[int(k * b)] = sec.get(int(k * b), 0) + v
        sx = sorted(sec)
        ax2.plot(sx, [sec[s] for s in sx], color=SER[2], lw=1.1, label="PUBACK/с")
        st = {}
        t0 = lg["t0"]
        for t, n in r["tput_series"]:
            st[t - int(t0)] = n
        tx = sorted(st)
        ax2.plot(tx, [st[t] for t in tx], color=SER[1], lw=1.1, ls="--", label="записано в БД/с")
        ax2.set_ylabel("сообщ./с", color=INK2)
        ax2.tick_params(colors=INK2, labelsize=8)
        ax.axvline(kill, color=RED, lw=1)
        ax.text(kill + 0.5, max(conn.values()) * 0.05, "kill emqx2", color=RED, fontsize=7.5)
        mode = "с буфером на устройстве" if r["buffer"] else "без буфера"
        ax.set_title(f"{name}: {mode}", fontsize=9, color=INK2)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, frameon=False, fontsize=7, loc="lower right", ncol=3)
        ax.set_xlim(kill - 10, lg["end_t"] - lg["t0"] + 8)
        style(ax)
    axes[-1, 0].set_xlabel("время от старта, с", color=INK2)
    fig.tight_layout()
    fig.savefig(img / "fig_failover.png", dpi=200)
    plt.close(fig)


def fig_herd(img):
    pols = [("herd-immediate", "сразу, затем каждую 1 с"), ("herd-exp", "экспоненциально 1, 2, 4… с, без джиттера"),
            ("herd-exp_jitter", "full jitter U(0, 1·2ⁿ) с"), ("herd-exp_jitter10", "full jitter U(0, 10·2ⁿ) с")]
    rows = [(n, t, load(n)) for n, t in pols]
    rows = [x for x in rows if x[2]]
    if not rows:
        return
    fig, axes = plt.subplots(len(rows), 1, figsize=(7.4, 1.85 * len(rows)), squeeze=False, sharex=True)
    for ax, (name, title, r) in zip(axes[:, 0], rows):
        lg = r["loadgen"]
        b, att = bins(r, "attempts")
        _, fails = bins(r, "fails")
        _, conn = bins(r, "connected")
        ev = r["event"]["t"] - lg["t0"]
        lo, hi = int((ev - 2) / b), int((ev + 86) / b)
        xs = list(range(lo, hi))
        ax.bar([k * b - ev for k in xs], [att.get(k, 0) / b for k in xs], width=b, color=SER[0], label="попыток CONNECT/с")
        ax.bar([k * b - ev for k in xs], [fails.get(k, 0) / b for k in xs], width=b, color=RED, label="из них неудачных")
        ax.set_ylabel("попыток/с", color=INK2)
        ax2 = ax.twinx()
        cx = [k for k in sorted(conn) if lo <= k < hi]
        ax2.plot([k * b - ev for k in cx], [conn[k] for k in cx], color=SER[2], lw=1.3, label="подключено")
        ax2.set_ylim(0, r["clients"] * 1.1)
        ax2.tick_params(colors=INK2, labelsize=8)
        peak = max((att.get(k, 0) for k in xs), default=0) / b
        rec = r["loadgen"]["counters"]["reconnects"]
        ax.set_title(f"{title}: пик {num(peak)} попыток/с, переподключилось {num(rec)} из {num(r['clients'])}",
                     fontsize=9, color=INK2)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        if name == rows[0][0]:
            ax.legend(h1 + h2, l1 + l2, frameon=True, framealpha=0.9, fontsize=7, loc="center right")
        style(ax)
    axes[-1, 0].set_xlabel("время после перезапуска NGINX, с (бины 0,1 с)", color=INK2)
    fig.tight_layout()
    fig.savefig(img / "fig_herd.png", dpi=200)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default="report/img")
    img = Path(ap.parse_args().img)
    img.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "Times New Roman", "font.size": 9})
    S = summary()
    fig_runs(S, img)
    fig_single(img)
    fig_scaling(S, img)
    fig_failover(img)
    fig_herd(img)
    print("ok:", sorted(p.name for p in img.glob("fig_*.png")))


if __name__ == "__main__":
    main()
