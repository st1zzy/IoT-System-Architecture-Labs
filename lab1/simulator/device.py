"""Уровень устройства (Lab 1).

[sensor models] → [sampling] → [validation] → [encoding] → [local ring buffer] → [MQTT publish]
Здесь — модели датчиков, валидация и сборка сообщения; кодирование — codec.py, отправка — runner.py.
"""

import math
import random
from collections import deque
from dataclasses import dataclass, field

NAN = float("nan")


@dataclass
class AnalogSensor:
    """Аналоговый канал: база + суточный цикл + дрейф + AR(1)-шум + сбои."""

    base: float
    amplitude: float          # суточная амплитуда (знак < 0 — минимум днём)
    noise: float              # СКО шума
    lo: float                 # физический диапазон датчика
    hi: float
    decimals: int
    drift_per_day: float = 0.0
    peak_utc_hour: float = 10.0       # максимум суточного цикла (10:00 UTC = 15:00 в Астане)
    spike_p: float = 0.001            # выброс ±3 амплитуды
    glitch_p: float = 0.0005          # сбой чтения по шине: заведомо невозможное значение
    glitch_value: float = -999.0
    nan_p: float = 0.0005             # датчик не ответил
    stuck_p: float = 0.0002           # «залипание»: значение перестаёт меняться
    unstuck_p: float = 0.02
    ar_phi: float = 0.7
    _ar: float = field(default=0.0, repr=False)
    _t0: float | None = field(default=None, repr=False)
    _stuck: float | None = field(default=None, repr=False)

    def read(self, t: float) -> float:
        if self._t0 is None:
            self._t0 = t
        if self._stuck is not None:
            if random.random() < self.unstuck_p:
                self._stuck = None
            else:
                return self._stuck
        if random.random() < self.nan_p:
            return NAN
        if random.random() < self.glitch_p:
            return self.glitch_value
        day = (t % 86400) / 86400 - self.peak_utc_hour / 24
        value = self.base + self.amplitude * math.cos(2 * math.pi * day)
        value += self.drift_per_day * (t - self._t0) / 86400
        self._ar = self.ar_phi * self._ar + random.gauss(0, self.noise)
        value += self._ar
        if random.random() < self.spike_p:
            value += random.choice([-1, 1]) * 3 * max(abs(self.amplitude), 5 * self.noise)
        value = round(value, self.decimals)
        if random.random() < self.stuck_p:
            self._stuck = value
        return value


@dataclass
class MotionSensor:
    """PIR: пуассоновский поток событий, днём интенсивнее. Канал = было ли движение за интервал."""

    day_rate_per_h: float = 6.0
    night_rate_per_h: float = 0.3

    def read(self, t: float, interval: float) -> bool:
        hour_local = ((t / 3600) + 5) % 24              # Астана, UTC+5
        rate = self.day_rate_per_h if 7 <= hour_local < 22 else self.night_rate_per_h
        return random.random() < 1 - math.exp(-rate * interval / 3600)


def default_sensors() -> dict[str, AnalogSensor]:
    return {
        "temperature": AnalogSensor(base=21.0, amplitude=4.0, noise=0.12, lo=-40, hi=85,
                                    decimals=2, drift_per_day=0.05, glitch_value=-144.0),
        "humidity":    AnalogSensor(base=45.0, amplitude=-10.0, noise=0.5, lo=0, hi=100,
                                    decimals=1, glitch_value=255.0),
        "pressure":    AnalogSensor(base=972.0, amplitude=1.5, noise=0.05, lo=300, hi=1100,
                                    decimals=2, peak_utc_hour=4.0, glitch_value=0.0),
    }


# --- validation -----------------------------------------------------------------

MAX_STEP = {"temperature": 3.0, "humidity": 10.0, "pressure": 3.0}   # допустимый скачок за 1 опрос
STUCK_N = 12                                                         # одинаковых отсчётов подряд (1 мин)
SPIKE_ACCEPT = 3                                                     # подряд «скачков» = новый уровень


class Validator:
    """Проверки на устройстве: NaN, физический диапазон, скачок, залипание.

    NaN и выход за диапазон — значение заведомо ложное, передаём null + флаг.
    Скачок и залипание — значение возможно, передаём как есть + флаг: решение за платформой.
    """

    def __init__(self, ranges: dict[str, tuple[float, float]]):
        self.ranges = ranges
        self.last: dict[str, float] = {}
        self.run: dict[str, int] = {}
        self.spikes: dict[str, int] = {}

    def check(self, name: str, v: float) -> tuple[float | None, list[str]]:
        if v is None or math.isnan(v):
            return None, ["nan"]
        lo, hi = self.ranges[name]
        if not lo <= v <= hi:
            return None, ["out_of_range"]
        flags = []
        prev = self.last.get(name)
        self.run[name] = self.run.get(name, 0) + 1 if prev == v else 1
        if self.run[name] >= STUCK_N:
            flags.append("stuck")
        if prev is not None and abs(v - prev) > MAX_STEP[name]:
            self.spikes[name] = self.spikes.get(name, 0) + 1
            if self.spikes[name] < SPIKE_ACCEPT:
                flags.append("spike")
                return v, flags                      # опорное значение не сдвигаем
        self.spikes[name] = 0
        self.last[name] = v
        return v, flags


class Device:
    def __init__(self, dev_id: str, site: str = "site1", interval: float = 5.0,
                 buffer_size: int = 500, battery_start: float = 87.0):
        self.id, self.site, self.interval = dev_id, site, interval
        self.seq = 0
        self.sensors = default_sensors()
        self.motion = MotionSensor()
        self.validator = Validator({k: (s.lo, s.hi) for k, s in self.sensors.items()})
        self.buffer: deque[dict] = deque(maxlen=buffer_size)    # кольцевой буфер
        self.dropped = 0
        self.battery = battery_start
        self.rssi_base = random.uniform(-75, -60)

    def sample(self, t: float) -> dict:
        self.seq += 1
        metrics, quality = {}, {}
        for name, sensor in self.sensors.items():
            value, flags = self.validator.check(name, sensor.read(t))
            metrics[name] = value
            if flags:
                quality[name] = flags
        metrics["motion"] = self.motion.read(t, self.interval)
        self.battery = max(0.0, self.battery - 0.0004)         # ≈ 7 %/сутки при 5 с — Wi-Fi
        msg = {
            "schema_version": "1.0",
            "device_id": self.id,
            "ts": int(t * 1000),                                # device_ts, мс UTC
            "seq": self.seq,
            "metrics": metrics,
            "battery": round(self.battery, 1),
            "rssi": max(-120, min(-30, round(random.gauss(self.rssi_base, 3)))),
        }
        if quality:
            msg["quality"] = quality
        return msg

    def enqueue(self, msg: dict) -> dict | None:
        """Политика переполнения drop_oldest: возвращает вытесненное сообщение."""
        evicted = self.buffer[0] if len(self.buffer) == self.buffer.maxlen else None
        if evicted is not None:
            self.dropped += 1
        self.buffer.append(msg)
        return evicted
