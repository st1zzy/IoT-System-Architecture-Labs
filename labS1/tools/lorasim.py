"""Дискретно-событийная модель LoRaWAN-сети с одним шлюзом (uplink, класс A).

Устройства передают без прослушивания эфира (ALOHA): периодически со случайной фазой и джиттером
либо пуассоновским потоком. Пакет теряется, если:
  coverage — RSSI ниже чувствительности SF;
  demod    — у шлюза заняты все демодуляторы (SX1301: 8 параллельных приёмов);
  collision — перекрытие во времени с пакетом того же SF на том же канале,
              кроме случая захвата: пакет сильнее каждого мешающего на CAPTURE_DB и более.
Разные SF считаются ортогональными (неидеальная ортогональность не моделируется).
"""

import heapq
from dataclasses import dataclass

import numpy as np

from lorawan import CAPTURE_DB, SENSITIVITY, TX_DBM, range_km, toa_s

CHANNELS = 8


@dataclass
class Result:
    sent: int
    no_coverage: int
    demod_drop: int
    collided: int
    captured: int            # пакеты, пережившие перекрытие благодаря захвату
    delivered: int
    offered_load: float      # G на канал (для одного SF)

    @property
    def pdr(self) -> float:
        return self.delivered / self.sent if self.sent else float("nan")


def place(n: int, radius_km: float, rng: np.random.Generator) -> np.ndarray:
    """Равномерно по площади круга вокруг шлюза (не ближе 50 м)."""
    return np.sqrt(rng.uniform((0.05 / radius_km) ** 2, 1, n)) * radius_km


def rx_power(d_km: np.ndarray, shadow_db: float, rng: np.random.Generator) -> np.ndarray:
    pl = 126.0 + 35.2 * np.log10(d_km)
    return TX_DBM - pl - (rng.normal(0, shadow_db, d_km.size) if shadow_db else 0.0)


def adr_sf(power_dbm: np.ndarray, margin_db: float = 10.0) -> np.ndarray:
    """Наименьший SF, у которого запас над чувствительностью ≥ margin (как ADR в ChirpStack)."""
    sf = np.full(power_dbm.size, 12)
    for s in range(11, 6, -1):
        sf[power_dbm - SENSITIVITY[s] >= margin_db] = s
    return sf


def simulate(n: int, sf, period_s: float, duration_s: float | None = None, *,
             radius_km: float | None = None, shadow_db: float = 0.0, capture: bool = True,
             demods: int | None = 8, channels: int = CHANNELS, arrivals: str = "periodic",
             jitter: float = 0.1, seed: int = 0, power=None, packets: bool = False):
    """sf — одно значение для всех устройств или массив по устройствам.

    Возвращает Result, а при packets=True — (Result, данные по каждой передаче).
    """
    rng = np.random.default_rng(seed)
    sf_dev = np.full(n, sf) if np.isscalar(sf) else np.asarray(sf)
    if power is None:
        r = radius_km if radius_km is not None else 0.9 * range_km(int(sf_dev.max()))
        power = rx_power(place(n, r, rng), shadow_db, rng)
    # ≥ 20 периодов и ≥ 20 000 передач, но не больше ~300 000 передач на прогон
    duration_s = duration_s or min(max(20 * period_s, 20_000 * period_s / n), 300_000 * period_s / n)
    duration_s = max(duration_s, 3 * period_s)

    # моменты начала передачи
    if arrivals == "poisson":
        k = rng.poisson(duration_s / period_s, n)
        dev = np.repeat(np.arange(n), k)
        start = rng.uniform(0, duration_s, dev.size)
    else:
        k = int(duration_s // period_s)
        phase = rng.uniform(0, period_s, n)
        dev = np.repeat(np.arange(n), k)
        idx = np.tile(np.arange(k), n)
        start = phase[dev] + idx * period_s + rng.uniform(-jitter, jitter, dev.size) * period_s
    ch = rng.integers(0, channels, dev.size)
    sfp = sf_dev[dev]
    pw = power[dev]
    toa = np.array([toa_s(s) if s >= 7 else 0.0 for s in range(13)])[sfp]
    end = start + toa
    sent = dev.size

    ok = pw >= np.array([SENSITIVITY.get(s, 0) for s in range(13)])[sfp]
    no_cov = int((~ok).sum())

    # демодуляторы шлюза: пакет, на преамбулу которого не нашлось свободного тракта, теряется
    demod_drop = np.zeros(sent, bool)
    if demods:
        order = np.argsort(start)
        busy: list[float] = []
        for i in order:
            if not ok[i]:
                continue
            t = start[i]
            while busy and busy[0] <= t:
                heapq.heappop(busy)
            if len(busy) >= demods:
                demod_drop[i] = True
            else:
                heapq.heappush(busy, end[i])
    alive = ok & ~demod_drop

    # коллизии внутри групп (канал, SF): все пакеты группы имеют одинаковый ToA
    collided = np.zeros(sent, bool)
    overlapped = np.zeros(sent, bool)
    for s in np.unique(sfp):
        for c in range(channels):
            g = np.flatnonzero((sfp == s) & (ch == c))          # мешают все передачи, даже без приёма
            if g.size < 2:
                continue
            g = g[np.argsort(start[g])]
            t, p, T = start[g], pw[g], toa_s(int(s))
            worst = np.full(g.size, -np.inf)                   # сильнейший мешающий для каждого
            for k_ in range(1, g.size):
                ov = (t[k_:] - t[:-k_]) < T
                if not ov.any():
                    break
                a, b = np.flatnonzero(ov), np.flatnonzero(ov) + k_
                np.maximum.at(worst, a, p[b])
                np.maximum.at(worst, b, p[a])
            hit = np.isfinite(worst)
            overlapped[g[hit]] = True
            lost = hit & ((p - worst) < CAPTURE_DB) if capture else hit
            collided[g[lost]] = True

    delivered = alive & ~collided
    captured = int((alive & overlapped & ~collided).sum())
    sf0 = int(sf_dev[0]) if np.isscalar(sf) else 0
    g_load = n * toa_s(sf0) / (period_s * channels) if sf0 else float("nan")
    res = Result(sent, no_cov, int(demod_drop.sum()), int((alive & collided).sum()), captured,
                 int(delivered.sum()), g_load)
    if packets:      # для моста в Network Server: по каждой передаче
        return res, {"dev": dev, "start": start, "sf": sfp, "ch": ch, "rssi": pw, "delivered": delivered}
    return res
