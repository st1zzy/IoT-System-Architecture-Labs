"""Сопоставление алармов с истинными эпизодами и «облачный» детектор по агрегатам 60 с.

Аларм засчитывается эпизоду, если совпадают устройство и метрика, а время аларма лежит
в [начало − 5 с, конец + 30 с]. recall = обнаруженные эпизоды / все; precision = верные алармы / все алармы.
"""

import statistics as st

MATCH_BEFORE, MATCH_AFTER = 5, 30


def match(truth: list[dict], alarms: list[dict]) -> dict:
    """truth: {dev, metric, type, start, end} (с); alarms: {dev, metric, t} (с от начала прогона)."""
    found = {}
    tp_alarms = 0
    for al in alarms:
        hit = None
        for i, e in enumerate(truth):
            if e["dev"] == al["dev"] and e["metric"] == al["metric"] and \
                    e["start"] - MATCH_BEFORE <= al["t"] <= e["end"] + MATCH_AFTER:
                hit = i
                break
        if hit is not None:
            tp_alarms += 1
            found.setdefault(hit, al["t"] - truth[hit]["start"])
    by_type = {}
    for i, e in enumerate(truth):
        b = by_type.setdefault(e["type"], {"n": 0, "found": 0, "delays": []})
        b["n"] += 1
        if i in found:
            b["found"] += 1
            b["delays"].append(found[i])
    for b in by_type.values():
        b["recall"] = b["found"] / b["n"] if b["n"] else float("nan")
        b["delay_median"] = st.median(b["delays"]) if b["delays"] else None
    return {"episodes": len(truth), "found": len(found), "alarms": len(alarms), "true_alarms": tp_alarms,
            "recall": len(found) / len(truth) if truth else float("nan"),
            "precision": tp_alarms / len(alarms) if alarms else float("nan"), "by_type": by_type,
            "missed": [truth[i] for i in range(len(truth)) if i not in found]}


def agg_detector(windows: dict[tuple[str, str], list[dict]]) -> list[dict]:
    """Облачный детектор, которому доступны только агрегаты окна: {t0, min, max, avg, std, p95}.

    spike — экстремум окна дальше 4,5 std от среднего; stuck — std окна < 25 % медианы ряда;
    drift — сдвиг среднего между соседними окнами > 4 медианных std. Время аларма — конец окна.
    """
    alarms = []
    for (dev, metric), ws in windows.items():
        ws = sorted(ws, key=lambda w: w["t0"])
        med = st.median([w["std"] for w in ws if w["std"] > 0] or [1e-9])
        last_alarm = -1e9
        for i, w in enumerate(ws):
            t = w["t0"] + 60
            hit = (w["std"] > 0 and max(w["max"] - w["avg"], w["avg"] - w["min"]) > 4.5 * w["std"]) or \
                  w["std"] < 0.25 * med or \
                  (i > 0 and abs(w["avg"] - ws[i - 1]["avg"]) > 4 * med)
            if hit and t - last_alarm >= 120:
                alarms.append({"dev": dev, "metric": metric, "t": t})
                last_alarm = t
    return alarms
