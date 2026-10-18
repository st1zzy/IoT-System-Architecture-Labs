"""Таблица «QoS × потери → доставлено / дублей / задержка» по results/qos/*.json.

    python tools/analyze_qos.py
"""

import json
import math
import statistics as st
import sys
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "results" / (sys.argv[1] if len(sys.argv) > 1 else "qos")
T95 = 4.303


def pct(v, p):
    s = sorted(v)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))] if s else float("nan")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    table = []
    print("QoS × потери (3 серии × 1000 сообщений): доставлено / дублей / задержка / разрывы")
    for loss in (0, 5, 20):
        for q in (0, 1, 2):
            runs = []
            for s in (1, 2, 3):
                run = f"q{q}_l{loss}_s{s}"
                f = RES / f"{run}_pub.json"
                # в архиве с окном 50 два прогона QoS 0 при 20 % завершились до записи файла издателя
                pub = json.loads(f.read_text()) if f.exists() else {"publish_err": 0, "disconnects": 1}
                sink = json.loads((RES / f"{run}_sink.json").read_text())
                runs.append((pub, sink))
            deliv = [sk["delivered"] / sk["n"] * 100 for _, sk in runs]
            lat = [x for _, sk in runs for x in sk["latency_ms"]]
            row = {
                "qos": q, "loss": loss, "sent": sum(sk["n"] for _, sk in runs),
                "delivered": sum(sk["delivered"] for _, sk in runs),
                "delivered_pct": st.mean(deliv),
                "delivered_ci": T95 * st.stdev(deliv) / math.sqrt(3),
                "dups": sum(sk["dups"] for _, sk in runs),
                "dup_flag": sum(sk["dup_flag"] for _, sk in runs),
                "dropped_qos0": sum(p["publish_err"] for p, _ in runs),
                "disconnects": sum(p["disconnects"] - 1 for p, _ in runs),     # минус штатное отключение
                "mean_ms": st.mean(lat), "p50_ms": pct(lat, 50), "p95_ms": pct(lat, 95), "p99_ms": pct(lat, 99),
                "max_ms": max(lat),
            }
            table.append(row)
            print(f"  потери {loss:>2} %  QoS {q}: доставлено {row['delivered']}/{row['sent']} "
                  f"({row['delivered_pct']:.2f} ± {row['delivered_ci']:.2f} %), дублей {row['dups']}, "
                  f"разрывов {row['disconnects']}, отброшено QoS 0 при разрыве {row['dropped_qos0']}; "
                  f"задержка ср. {row['mean_ms']:.0f}, p50 {row['p50_ms']:.0f}, p99 {row['p99_ms']:.0f}, "
                  f"макс {row['max_ms']:.0f} мс")
    (RES.parent / f"{RES.name}_summary.json").write_text(json.dumps(table, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
