"""Модель сигналов устройств и расписание аномалий (общий код симулятора и офлайн-подбора порогов).

Каждая метрика: база + медленная синусоида (процесс, амплитуда 3σ) + белый шум σ, округление до шага
меньше σ. Аномалии (по 90 с, кроме выброса): spike — один отсчёт со смещением 8–15σ; drift — линейный
уход 0,25σ/с (к концу ≈ 22σ), затем возврат; stuck — значение замирает.
"""

import math
import random
from dataclasses import dataclass

METRICS = {
    "temperature": {"base": 40.0, "sigma": 0.3, "period": 900, "dec": 2},
    "humidity": {"base": 50.0, "sigma": 1.0, "period": 1200, "dec": 1},
    "pressure": {"base": 1.0, "sigma": 0.005, "period": 700, "dec": 3},
    "vibration": {"base": 2.0, "sigma": 0.2, "period": 800, "dec": 2},
    "current": {"base": 10.0, "sigma": 0.1, "period": 600, "dec": 3},
}
TYPES = ("spike", "drift", "stuck")
EPISODE_S = 90
DRIFT_RATE = 0.25            # σ в секунду


@dataclass
class Anomaly:
    dev: int
    metric: str
    type: str
    start: int               # секунда от начала прогона
    end: int
    sign: int = 1
    mag: float = 10.0        # для spike — смещение в σ

    def as_dict(self) -> dict:
        return {"dev": f"dev-{self.dev:03d}", "metric": self.metric, "type": self.type, "start": self.start,
                "end": self.end, "sign": self.sign, "mag": round(self.mag, 2)}


def schedule(seed: int, n_dev: int, duration: int, n_anom: int, warmup: int = 120) -> list[Anomaly]:
    """n_anom аномалий, типы поровну, на одной паре устройство–метрика не чаще раза в 300 с."""
    rng = random.Random(seed)
    out: list[Anomaly] = []
    busy: dict[tuple[int, str], list[int]] = {}
    i = 0
    while len(out) < n_anom:
        dev, metric = rng.randrange(n_dev), rng.choice(list(METRICS))
        start = rng.randrange(warmup, duration - EPISODE_S - 30)
        if any(abs(start - s) < 300 for s in busy.get((dev, metric), [])):
            continue
        typ = TYPES[i % len(TYPES)]
        i += 1
        end = start + 1 if typ == "spike" else start + EPISODE_S
        out.append(Anomaly(dev, metric, typ, start, end, rng.choice((-1, 1)), rng.uniform(8, 15)))
        busy.setdefault((dev, metric), []).append(start)
    return sorted(out, key=lambda a: a.start)


class Device:
    def __init__(self, idx: int, rng: random.Random):
        self.idx, self.rng = idx, rng
        self.phase = {m: rng.uniform(0, 2 * math.pi) for m in METRICS}
        self.offset = {m: rng.gauss(0, 2) * p["sigma"] for m, p in METRICS.items()}
        self.anom: dict[str, Anomaly] = {}
        self.frozen: dict[str, float] = {}

    def sample(self, k: int) -> dict[str, float]:
        out = {}
        for m, p in METRICS.items():
            a = self.anom.get(m)
            if a and a.type == "stuck" and a.start <= k < a.end and m in self.frozen:
                out[m] = self.frozen[m]
                continue
            s = p["sigma"]
            v = p["base"] + self.offset[m] + 3 * s * math.sin(2 * math.pi * k / p["period"] + self.phase[m])
            v += self.rng.gauss(0, s)
            if a and a.start <= k < a.end:
                if a.type == "spike":
                    v += a.sign * a.mag * s
                elif a.type == "drift":
                    v += a.sign * DRIFT_RATE * s * (k - a.start)
            v = round(v, p["dec"])
            if a and a.type == "stuck" and k == a.start:
                self.frozen[m] = v
            out[m] = v
        return out
