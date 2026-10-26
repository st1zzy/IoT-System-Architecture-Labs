"""Детектор аномалий одного ряда (устройство × метрика) — один и тот же код на edge и в облаке.

σ̂ — оценка шума по модулю разности соседних отсчётов (E|Δ| = 1,128σ): медленные тренды и дрейф её
не раздувают. spike: |x − EWMA_fast| > K_SPIKE · σ̂; три «выброса» подряд — сдвиг уровня (например,
возврат после дрейфа или залипания): средние переинициализируются, иначе каждая следующая точка была бы
выбросом. drift: |EWMA_fast − EWMA_slow| > K_DRIFT · σ̂. stuck: STUCK_N одинаковых отсчётов подряд.
После аларма по ряду — пауза HOLDOFF_S, чтобы один эпизод давал один аларм.
"""

K_SPIKE, K_DRIFT = 6.0, 7.0
A_FAST, A_SLOW, A_SIG = 0.2, 1 / 60, 0.02
STUCK_N, WARMUP, HOLDOFF_S, SHIFT_N = 15, 60, 120, 3


class Detector:
    __slots__ = ("n", "fast", "slow", "sig", "last", "run", "out", "alarm_ts")

    def __init__(self):
        self.n, self.fast, self.slow, self.sig = 0, 0.0, 0.0, None
        self.last, self.run, self.out, self.alarm_ts = None, 0, 0, -1e18

    def update(self, v: float, ts: float) -> tuple[str, float] | None:
        """ts в секундах. Возвращает (тип, оценка) при новом эпизоде, иначе None."""
        self.n += 1
        if self.n == 1:
            self.fast = self.slow = v
            self.last, self.run = v, 1
            return None
        diff = abs(v - self.last)
        self.run = self.run + 1 if v == self.last else 1
        self.last = v
        event = None
        if self.run >= STUCK_N:
            event = ("stuck", float(self.run))
        dev = v - self.fast
        spike = self.sig is not None and self.n > WARMUP and abs(dev) > K_SPIKE * self.sig
        if spike:
            self.out += 1
            if event is None:
                event = ("spike", abs(dev) / self.sig)
            if self.out >= SHIFT_N:               # устойчивый сдвиг уровня — принять новый уровень
                self.fast = self.slow = v
                self.out = 0
        else:
            clean = self.out == 0                 # разность после выброса загрязнена — не учитывать
            self.out = 0
            self.fast += A_FAST * dev
            self.slow += A_SLOW * (v - self.slow)
            if clean and self.run < 3:            # залипание не должно «сжимать» оценку шума
                s = diff / 1.128
                self.sig = s if self.sig is None else self.sig + A_SIG * (s - self.sig)
        if event is None and self.n > WARMUP and self.sig and abs(self.fast - self.slow) > K_DRIFT * self.sig:
            event = ("drift", abs(self.fast - self.slow) / self.sig)
        if event and ts - self.alarm_ts >= HOLDOFF_S:
            self.alarm_ts = ts
            return event
        return None
