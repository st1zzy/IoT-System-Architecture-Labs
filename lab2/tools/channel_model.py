"""Модель Wi-Fi канала → параметры tc netem для пяти расстояний.

RSSI(d) = P_tx − PL(d0) − 10·n·lg(d/d0)          — логарифмическая модель потерь (2,4 ГГц, офис)
MCS     = старшая схема, у которой RSSI ≥ чувствительность + запас 3 дБ
          (минимальная чувствительность 802.11n HT20, IEEE 802.11-2012, табл. 20-23)
rate    = PHY-скорость MCS × КПД MAC (0,5: заголовки, ACK, конкуренция за канал)
loss    = потери после MAC-повторов на каждое направление; уровни выбраны так, чтобы
          в оба конца получилось 0/1/3/10/20 % (уровни силлабуса) и потери росли
          по мере приближения RSSI к порогу чувствительности
delay   = задержка в одну сторону ± джиттер (ожидание канала и MAC-повторы)

    python tools/channel_model.py           — таблица профилей
"""

import math
from dataclasses import dataclass

P_TX_DBM = 15.0          # эффективная мощность передачи ESP32 с учётом антенн
PL_D0_DB = 50.0          # потери на опорном расстоянии 1 м
N_EXP = 3.3              # показатель затухания: офис со стенами
MARGIN_DB = 3.0          # запас rate control над чувствительностью
MAC_EFFICIENCY = 0.5

# MCS: (чувствительность, дБм; PHY-скорость HT20 1SS long GI, Мбит/с)
MCS = [(-82, 6.5), (-79, 13.0), (-77, 19.5), (-74, 26.0),
       (-70, 39.0), (-66, 52.0), (-65, 58.5), (-64, 65.0)]


@dataclass(frozen=True)
class Profile:
    name: str
    distance_m: float
    loss_pct: float          # на каждое направление
    delay_ms: float          # в одну сторону
    jitter_ms: float

    @property
    def rssi(self) -> float:
        return rssi_at(self.distance_m)

    @property
    def mcs(self) -> int:
        ok = [i for i, (sens, _) in enumerate(MCS) if self.rssi >= sens + MARGIN_DB]
        return ok[-1] if ok else 0          # ниже порога — MCS0 на пределе связи

    @property
    def phy_mbps(self) -> float:
        return MCS[self.mcs][1]

    @property
    def rate_mbps(self) -> float:
        return round(self.phy_mbps * MAC_EFFICIENCY, 2)

    @property
    def margin_db(self) -> float:
        return self.rssi - MCS[self.mcs][0]

    def netem(self) -> str:
        return (f"delay {self.delay_ms}ms {self.jitter_ms}ms 25% distribution normal "
                f"loss {self.loss_pct}% rate {self.rate_mbps}mbit")


def rssi_at(d: float) -> float:
    return P_TX_DBM - PL_D0_DB - 10 * N_EXP * math.log10(max(d, 0.1))


PROFILES = [
    Profile("d01", 1, 0.0, 1, 0.5),
    Profile("d05", 5, 0.5, 2, 1),
    Profile("d10", 10, 1.5, 4, 2),
    Profile("d20", 20, 5.0, 8, 4),
    Profile("d30", 30, 10.0, 15, 8),
]


if __name__ == "__main__":
    print(f"{'профиль':<8}{'d, м':>6}{'RSSI':>8}{'MCS':>5}{'запас':>7}{'PHY':>7}{'rate':>7}"
          f"{'потери':>8}{'RTT~':>7}  netem")
    for p in PROFILES:
        rt = 1 - (1 - p.loss_pct / 100) ** 2
        print(f"{p.name:<8}{p.distance_m:>6g}{p.rssi:>8.1f}{p.mcs:>5}{p.margin_db:>6.1f}"
              f"{p.phy_mbps:>7g}{p.rate_mbps:>7g}{100 * rt:>7.1f}%{2 * p.delay_ms:>6g}  {p.netem()}")
