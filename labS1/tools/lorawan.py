"""Физика и энергетика LoRaWAN EU868 для симулятора.

Time on Air — Semtech SX1276 datasheet / AN1200.13:
    T_sym = 2^SF / BW
    T_preamble = (n_preamble + 4,25) · T_sym
    n_payload = 8 + max(ceil((8·PL − 4·SF + 28 + 16·CRC − 20·IH) / (4·(SF − 2·DE))) · (CR + 4), 0)
Потери на трассе — Окумура–Хата, город, 868 МГц, шлюз 30 м, устройство 1,5 м:
    PL(d) = 126,0 + 35,2·lg(d, км)
"""

import math

BW = 125_000
N_PREAMBLE = 8
CR = 1                       # 4/5
PHY_OVERHEAD = 13            # MHDR 1 + DevAddr 4 + FCtrl 1 + FCnt 2 + FPort 1 + MIC 4
APP_PAYLOAD = 7              # двоичная упаковка измерений Lab 1: t int16, h uint8, p uint16, bat, flags
TX_DBM = 14.0                # EU868, максимальная ERP для большинства суб-полос
SENSITIVITY = {7: -123.0, 8: -126.0, 9: -129.0, 10: -132.0, 11: -134.5, 12: -137.0}   # SX1276, 125 кГц
CAPTURE_DB = 6.0             # перевес по мощности, при котором сильный пакет выживает (co-SF)
DUTY_CYCLE = 0.01            # EU868, суб-полоса g1


def toa_s(sf: int, payload: int = APP_PAYLOAD) -> float:
    pl = PHY_OVERHEAD + payload
    de = 1 if sf >= 11 else 0                       # Low Data Rate Optimize для SF11/12 на 125 кГц
    t_sym = (2 ** sf) / BW
    n = 8 + max(math.ceil((8 * pl - 4 * sf + 28 + 16) / (4 * (sf - 2 * de))) * (CR + 4), 0)
    return (N_PREAMBLE + 4.25) * t_sym + n * t_sym


def t_sym(sf: int) -> float:
    return (2 ** sf) / BW


def path_loss_db(d_km):
    return 126.0 + 35.2 * math.log10(max(d_km, 0.01))


def range_km(sf: int, margin_db: float = 0.0) -> float:
    """Дальность без затенения: RSSI = TX − PL ≥ чувствительность + запас."""
    return 10 ** ((TX_DBM - SENSITIVITY[sf] - margin_db - 126.0) / 35.2)


# --- энергетика (класс A), токи по §14.3.2 силлабуса и SX1276 -----------------------
I_SLEEP_MA = 0.002
PHASES_FIXED = [                     # (название, мА, с)
    ("пробуждение и опрос датчика", 8.0, 0.30),
    ("подготовка пакета", 6.0, 0.05),
    ("возврат в сон", 5.0, 0.02),
]
I_TX_MA, I_RX_MA = 45.0, 12.0
RX_SYMBOLS = 8                       # окно открыто, пока ищется преамбула
RX2_SF = 12                          # RX2 по умолчанию EU868: 869,525 МГц, SF12 (DR0)
BATTERY_MAH, K_DERATING = 3000.0, 0.75


def energy_phases(sf: int, rx2_sf: int = RX2_SF) -> list[tuple[str, float, float]]:
    return [PHASES_FIXED[0], PHASES_FIXED[1],
            ("передача (ToA)", I_TX_MA, toa_s(sf)),
            ("окно RX1", I_RX_MA, RX_SYMBOLS * t_sym(sf)),
            (f"окно RX2 (SF{rx2_sf})", I_RX_MA, RX_SYMBOLS * t_sym(rx2_sf)),
            PHASES_FIXED[2]]


def e_msg_uah(sf: int, rx2_sf: int = RX2_SF) -> float:
    return sum(i * t for _, i, t in energy_phases(sf, rx2_sf)) * 1000 / 3600


def lifetime_years(sf: int, msgs_per_hour: float, rx2_sf: int = RX2_SF) -> float:
    i_avg = I_SLEEP_MA + e_msg_uah(sf, rx2_sf) * msgs_per_hour / 1000
    return BATTERY_MAH * K_DERATING / i_avg / 8760


def min_period_s(sf: int) -> float:
    """Минимальный интервал передачи, разрешённый duty cycle 1 %."""
    return toa_s(sf) / DUTY_CYCLE


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"PHY payload {PHY_OVERHEAD + APP_PAYLOAD} Б (заголовки LoRaWAN {PHY_OVERHEAD} + данные {APP_PAYLOAD})")
    print(f"{'SF':>3}{'ToA, мс':>10}{'T_sym, мс':>11}{'чувств.':>9}{'дальность':>11}{'мин. интервал':>15}{'E_msg, мкА·ч':>14}")
    for sf in range(7, 13):
        print(f"{sf:>3}{toa_s(sf) * 1000:>10.1f}{t_sym(sf) * 1000:>11.3f}{SENSITIVITY[sf]:>9.1f}"
              f"{range_km(sf):>9.2f} км{min_period_s(sf):>13.1f} с{e_msg_uah(sf):>14.2f}")
    print("Проверка §10.3.3 (payload 20 Б):",
          ", ".join(f"SF{sf} {toa_s(sf, 20 - PHY_OVERHEAD) * 1000:.1f} мс" for sf in (7, 9, 10, 12)))
