"""Все эксперименты Lab S1 → results/*.json и сводка в консоль.

    python tools/experiments.py
"""

import json
import math
import statistics as st
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from lorasim import adr_sf, place, rx_power, simulate  # noqa: E402
from lorawan import (DUTY_CYCLE, SENSITIVITY, e_msg_uah, energy_phases, lifetime_years,  # noqa: E402
                     min_period_s, range_km, toa_s)

OUT = Path(__file__).resolve().parent.parent / "results"
SEEDS = (1, 2, 3)
T95 = 4.303                                    # t-квантиль, 3 повтора
N_GRID = [100, 250, 500, 1000, 2000, 5000, 10000]
SF_GRID = [7, 9, 12]
PERIODS = [300, 600, 3600]


def mean_ci(v):
    return st.mean(v), T95 * st.stdev(v) / math.sqrt(len(v))


def run(n, sf, period, **kw):
    rs = [simulate(n, sf, period, seed=s, **kw) for s in SEEDS]
    pdr = mean_ci([r.pdr for r in rs])
    tot = sum(r.sent for r in rs)
    return {"n": n, "sf": sf, "period": period, "pdr": pdr[0], "pdr_ci": pdr[1],
            "G": rs[0].offered_load, "sent": tot,
            "collided": sum(r.collided for r in rs) / tot, "demod": sum(r.demod_drop for r in rs) / tot,
            "no_coverage": sum(r.no_coverage for r in rs) / tot,
            "captured": sum(r.captured for r in rs) / tot}


def max_devices(sf, period, capture, target=0.95):
    lo, hi = 10, 30_000
    while hi - lo > max(1, 0.01 * lo):                    # точность 1 %
        mid = int(math.sqrt(lo * hi)) if hi > 2 * lo else (lo + hi) // 2
        pdr = st.mean(simulate(mid, sf, period, seed=s, capture=capture).pdr for s in SEEDS)
        lo, hi = (mid, hi) if pdr >= target else (lo, mid)
    return lo


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    OUT.mkdir(exist_ok=True)

    # 1. проверка на аналитическом пределе чистой ALOHA
    print("1) Проверка модели: чистая ALOHA (1 канал, пуассоновский поток, без захвата и лимита демодуляторов)")
    aloha = []
    for G in [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5, 2.0, 3.0]:
        n, T = 500, toa_s(7)
        per = n * T / G
        pdr = [simulate(n, 7, per, duration_s=per * 200, capture=False, demods=None, channels=1,
                        arrivals="poisson", seed=s).pdr for s in SEEDS]
        S = G * st.mean(pdr)
        aloha.append({"G": G, "S_sim": S, "S_theory": G * math.exp(-2 * G), "pdr": st.mean(pdr)})
        print(f"   G={G:4.2f}  S_sim={S:.4f}  S_теор={G * math.exp(-2 * G):.4f}  PDR={st.mean(pdr):.4f}")
    best = max(aloha, key=lambda a: a["S_sim"])
    print(f"   максимум: S={best['S_sim']:.4f} при G={best['G']}; теория 1/(2e) = {1 / (2 * math.e):.4f} при G=0,5")
    (OUT / "aloha.json").write_text(json.dumps(aloha, indent=2), encoding="utf-8")

    # 2. сетка PDR(N, SF, период) с захватом и без
    print("2) PDR(N, SF, интервал): 8 каналов, 8 демодуляторов, устройства в зоне покрытия своего SF")
    grid = []
    for cap in (True, False):
        for per in PERIODS:
            for sf in SF_GRID:
                row = []
                for n in N_GRID:
                    r = run(n, sf, per, capture=cap)
                    r["capture"] = cap
                    grid.append(r)
                    row.append(f"{100 * r['pdr']:5.1f}")
                dc = toa_s(sf) / per
                print(f"   захват={'да ' if cap else 'нет'} T={per:>4} с SF{sf:<2} (DC {100 * dc:.2f} %): "
                      + " ".join(f"N={n}:{v}%" for n, v in zip(N_GRID, row)))
    (OUT / "grid.json").write_text(json.dumps(grid, indent=2), encoding="utf-8")

    # 3. максимум устройств на шлюз при PDR ≥ 95 %
    print("3) Максимум устройств на шлюз при PDR ≥ 95 % (бисекция, 3 повтора)")
    cap_tab = []
    for per in PERIODS:
        for sf in SF_GRID:
            a, b = max_devices(sf, per, True), max_devices(sf, per, False)
            g = 0.5 * math.log(1 / 0.95)                 # аналитика ALOHA: e^(−2G) = 0,95
            n_th = g * 8 * per / toa_s(sf)
            cap_tab.append({"sf": sf, "period": per, "with_capture": a, "no_capture": b, "aloha_theory": n_th})
            print(f"   T={per:>4} с SF{sf:<2}: с захватом {a:>6}, без захвата {b:>6}, ALOHA-теория {n_th:7.0f}")
    (OUT / "capacity.json").write_text(json.dumps(cap_tab, indent=2), encoding="utf-8")

    # 4. реалистичная сота: радиус 5 км, затенение 6 дБ; ADR против «все на SF12»
    print("4) Соты R = 3 и 5 км, затенение σ = 6 дБ, интервал 600 с: ADR (запас 10 дБ) против «все на SF12»")
    adr = []
    for radius, n, mode in [(r, n, m) for r in (3.0, 5.0) for n in (500, 1000, 2000, 5000) for m in ("adr", "sf12")]:
        if True:
            res = []
            for s in SEEDS:
                rng = np.random.default_rng(100 + s)
                pw = rx_power(place(n, radius, rng), 6.0, rng)
                sf = adr_sf(pw) if mode == "adr" else np.full(n, 12)
                r = simulate(n, sf, 600, power=pw, seed=s)
                e = float(np.mean([e_msg_uah(int(x)) for x in sf]))
                dist = {int(k): int(v) for k, v in zip(*np.unique(sf, return_counts=True))}
                res.append((r, e, dist))
            pdr = mean_ci([r.pdr for r, _, _ in res])
            tot = sum(r.sent for r, _, _ in res)
            dist = {k: sum(d.get(k, 0) for _, _, d in res) / (len(SEEDS) * n) for k in range(7, 13)}
            row = {"radius_km": radius, "n": n, "mode": mode, "pdr": pdr[0], "pdr_ci": pdr[1],
                   "no_coverage": sum(r.no_coverage for r, _, _ in res) / tot,
                   "collided": sum(r.collided for r, _, _ in res) / tot,
                   "demod": sum(r.demod_drop for r, _, _ in res) / tot,
                   "e_msg_uah": st.mean(e for _, e, _ in res), "sf_share": dist}
            adr.append(row)
            print(f"   R={radius:g} км N={n:>5} {mode:<4}: PDR {100 * pdr[0]:5.1f} ± {100 * pdr[1]:3.1f} %  "
                  f"(вне покрытия {100 * row['no_coverage']:4.1f}, коллизии {100 * row['collided']:4.1f}, "
                  f"демодуляторы {100 * row['demod']:4.1f} %)  E_msg {row['e_msg_uah']:5.2f} мкА·ч  "
                  f"SF7..12: " + "/".join(f"{100 * dist[k]:.0f}" for k in range(7, 13)))
    (OUT / "adr.json").write_text(json.dumps(adr, indent=2), encoding="utf-8")

    # 5. энергобюджет
    print("5) Энергия одного сообщения (класс A, RX2 = SF12 по умолчанию EU868)")
    energy = {}
    for sf in SF_GRID:
        ph = energy_phases(sf)
        tot = sum(i * t for _, i, t in ph)
        energy[sf] = [{"phase": n, "ma": i, "s": t, "uah": i * t / 3.6, "share": i * t / tot} for n, i, t in ph]
        print(f"   SF{sf}: E_msg = {e_msg_uah(sf):.2f} мкА·ч; " +
              "; ".join(f"{p['phase']} {p['uah']:.2f} ({100 * p['share']:.0f} %)" for p in energy[sf]))
    print(f"   для сравнения RX2 = SF9 (как в TTN): " +
          ", ".join(f"SF{sf} {e_msg_uah(sf, 9):.2f}" for sf in SF_GRID) + " мкА·ч")
    life = []
    rates = [1 / 24, 1, 4, 12, 60]
    print("   Срок службы, лет (3000 мА·ч × 0,75; I_sleep 2 мкА); * — нарушен duty cycle 1 %")
    for sf in SF_GRID:
        row = {"sf": sf, "years": {}, "dc_ok": {}}
        for r in rates:
            row["years"][r] = lifetime_years(sf, r)
            row["dc_ok"][r] = 3600 / r >= min_period_s(sf)
        life.append(row)
        print(f"   SF{sf:<2} " + "  ".join(f"{r:g}/ч: {row['years'][r]:7.2f}{'' if row['dc_ok'][r] else '*'}" for r in rates))
    (OUT / "energy.json").write_text(json.dumps({"phases": energy, "life": life,
                                                 "rx2_sf9": {sf: e_msg_uah(sf, 9) for sf in SF_GRID}},
                                                indent=2, ensure_ascii=False), encoding="utf-8")
    print("   справка: ToA " + ", ".join(f"SF{s} {1000 * toa_s(s):.1f} мс" for s in range(7, 13))
          + f"; дальность SF7 {range_km(7):.2f} км, SF12 {range_km(12):.2f} км; DC {100 * DUTY_CYCLE:g} %"
          + f"; чувствительность SF7 {SENSITIVITY[7]} … SF12 {SENSITIVITY[12]} дБм")


if __name__ == "__main__":
    main()
