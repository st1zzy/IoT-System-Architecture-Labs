"""Проверка схемы и валидации на устройстве.

1) N сообщений модели с вероятностями сбоев ×K — все должны пройти схему, флаги считаются по каналам;
2) набор заведомо неверных сообщений — каждое должно быть отклонено схемой.
    python tools/validate_samples.py --n 20000 --boost 10 --out results/validation.json
"""

import argparse
import copy
import json
import random
import sys
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "simulator"))
from device import Device  # noqa: E402

NEGATIVE = {
    "нет поля seq": lambda m: m.pop("seq"),
    "seq отрицательный": lambda m: m.update(seq=-1),
    "ts строкой (ISO 8601)": lambda m: m.update(ts="2026-10-18T00:00:00Z"),
    "temperature строкой": lambda m: m["metrics"].update(temperature="21.5"),
    "humidity = 150 %": lambda m: m["metrics"].update(humidity=150.0),
    "нет канала motion": lambda m: m["metrics"].pop("motion"),
    "неизвестное поле firmware": lambda m: m.update(firmware="1.2.3"),
    "schema_version = 2.0": lambda m: m.update(schema_version="2.0"),
    "device_id с пробелом": lambda m: m.update(device_id="room sensor"),
    "неизвестный флаг quality": lambda m: m.update(quality={"temperature": ["broken"]}),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--boost", type=float, default=10, help="множитель вероятностей сбоев")
    ap.add_argument("--out")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    random.seed(7)
    schema = json.load(open(ROOT / "schema" / "telemetry.schema.json", encoding="utf-8"))
    v = Draft202012Validator(schema)

    dev = Device("room-sensor-01")
    for s in dev.sensors.values():
        for p in ("spike_p", "glitch_p", "nan_p", "stuck_p"):
            setattr(s, p, getattr(s, p) * args.boost)
    t0 = 1792281600.0
    msgs = [dev.sample(t0 + i * 5) for i in range(args.n)]

    invalid = sum(1 for m in msgs if not v.is_valid(m))
    flags = Counter()
    for m in msgs:
        for ch, fl in m.get("quality", {}).items():
            for f in fl:
                flags[(ch, f)] += 1
    with_q = sum(1 for m in msgs if "quality" in m)

    print(f"Сгенерировано {args.n} сообщений (вероятности сбоев ×{args.boost:g})")
    print(f"  не прошли схему: {invalid}")
    print(f"  с флагами quality: {with_q} ({100 * with_q / args.n:.1f} %)")
    print(f"  {'канал':<12}{'nan':>6}{'out_of_range':>14}{'spike':>8}{'stuck':>8}")
    table = {}
    for ch in ("temperature", "humidity", "pressure"):
        row = {f: flags[(ch, f)] for f in ("nan", "out_of_range", "spike", "stuck")}
        table[ch] = row
        print(f"  {ch:<12}{row['nan']:>6}{row['out_of_range']:>14}{row['spike']:>8}{row['stuck']:>8}")

    print("Негативные тесты (ожидается отклонение):")
    neg = {}
    base = next(m for m in msgs if "quality" not in m)
    for name, mutate in NEGATIVE.items():
        m = copy.deepcopy(base)
        mutate(m)
        errs = list(v.iter_errors(m))
        neg[name] = errs[0].message if errs else None
        print(f"  {'ОТКЛОНЕНО' if errs else 'ПРИНЯТО!':<10} {name:<28} {errs[0].message[:70] if errs else ''}")
    rejected = sum(1 for e in neg.values() if e)
    print(f"Итог: {args.n - invalid}/{args.n} корректных приняты, {rejected}/{len(NEGATIVE)} неверных отклонены")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"n": args.n, "boost": args.boost, "schema_invalid": invalid,
                       "with_quality": with_q, "flags": table, "negative": neg}, f,
                      indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
