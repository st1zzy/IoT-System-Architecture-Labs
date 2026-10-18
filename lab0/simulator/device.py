"""Модель IoT-устройства для software-only варианта курса (§26 силлабуса)."""

import math
import random
from collections import deque
from dataclasses import dataclass, field


@dataclass
class SensorModel:
    """Датчик: базовое значение + суточный цикл + дрейф + AR(1)-шум + аномалии."""

    base: float
    amplitude: float = 0.0       # суточная амплитуда
    noise: float = 0.1           # СКО шума
    drift_per_day: float = 0.0   # дрейф калибровки
    lo: float = -1e9
    hi: float = 1e9
    anomaly_p: float = 0.0005    # вероятность выброса
    stuck_p: float = 0.0001      # вероятность «залипания»
    ar_phi: float = 0.6          # коэффициент AR(1)
    _stuck_value: float | None = field(default=None, repr=False)
    _ar_state: float = field(default=0.0, repr=False)
    _t0: float | None = field(default=None, repr=False)   # момент включения — отсчёт дрейфа

    def read(self, t: float) -> float:
        if self._t0 is None:
            self._t0 = t
        if self._stuck_value is not None:
            if random.random() < 0.02:          # выход из залипания
                self._stuck_value = None
            else:
                return self._stuck_value
        day = (t % 86400) / 86400
        value = self.base + self.amplitude * math.sin(2 * math.pi * day)
        value += self.drift_per_day * ((t - self._t0) / 86400)
        self._ar_state = self.ar_phi * self._ar_state + random.gauss(0, self.noise)
        value += self._ar_state
        if random.random() < self.anomaly_p:
            value += random.choice([-1, 1]) * abs(self.amplitude) * 3
        value = round(max(self.lo, min(self.hi, value)), 3)
        if random.random() < self.stuck_p:
            self._stuck_value = value
        return value


def default_sensors() -> dict[str, SensorModel]:
    return {
        "temperature": SensorModel(base=22, amplitude=6, noise=0.25, lo=-40, hi=85,
                                   drift_per_day=0.002),
        "humidity":    SensorModel(base=55, amplitude=-12, noise=1.2, lo=0, hi=100),
        "pressure":    SensorModel(base=1013, amplitude=1.5, noise=0.3, lo=870, hi=1080),
        "light":       SensorModel(base=400, amplitude=380, noise=25, lo=0, hi=100000),
    }


class Device:
    """Виртуальное устройство: опрос датчиков → сообщение → локальный буфер."""

    def __init__(self, dev_id: str, site: str = "site1", interval: float = 10.0,
                 metrics: list[str] | None = None, buffer_size: int = 500):
        self.id = dev_id
        self.site = site
        self.interval = interval
        self.seq = 0
        sensors = default_sensors()
        self.sensors = {k: sensors[k] for k in (metrics or sensors)}
        # Кольцевой буфер store-and-forward: при переполнении вытесняются самые старые
        self.buffer: deque[dict] = deque(maxlen=buffer_size)
        self.dropped = 0

    def sample(self, t: float) -> dict:
        self.seq += 1
        return {
            "schema_version": "1.0",
            "device_id": self.id,
            "site": self.site,
            "ts": round(t, 3),
            "seq": self.seq,
            "metrics": {k: s.read(t) for k, s in self.sensors.items()},
        }

    def enqueue(self, msg: dict) -> None:
        if len(self.buffer) == self.buffer.maxlen:
            self.dropped += 1
        self.buffer.append(msg)
