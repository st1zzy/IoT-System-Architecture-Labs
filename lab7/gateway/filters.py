"""Deadband-фильтр шлюза: публиковать значение, только если оно заметно изменилось.

mode: off — всё; abs — |v − v_посл| ≥ порог в единицах тега; pct — ≥ порог·|v_посл|/100.
Дискретные теги публикуются при любом изменении. Heartbeat: не реже heartbeat_s даже без изменений,
иначе на приёмной стороне «датчик молчит» неотличимо от «значение стабильно».
"""


class Deadband:
    def __init__(self, mode: str, abs_th: float = 0.0, pct_th: float = 0.0, heartbeat_s: float = 60.0,
                 discrete: bool = False, scale: float = 1.0):
        self.mode, self.discrete, self.heartbeat_ms = mode, discrete, heartbeat_s * 1000
        self.abs_th, self.pct_th = abs_th * scale, pct_th * scale
        self.last: float | None = None
        self.last_ts = 0

    def check(self, v: float, ts_ms: int) -> str | None:
        """Причина публикации ('first', 'change', 'heartbeat') или None — отфильтровано."""
        if self.last is None:
            reason = "first"
        elif self.discrete or self.mode == "off":
            reason = "change" if (self.mode == "off" or v != self.last) else None
        elif self.mode == "abs":
            reason = "change" if abs(v - self.last) >= self.abs_th else None
        else:
            reason = "change" if abs(v - self.last) >= self.pct_th / 100 * abs(self.last) and v != self.last else None
        if reason is None and ts_ms - self.last_ts >= self.heartbeat_ms:
            reason = "heartbeat"
        if reason:
            self.last, self.last_ts = v, ts_ms
        return reason
