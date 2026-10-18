# Lab 1. Device Layer — датчики и модель данных

Отчёт: [Lab1_Жанайдаров_Амирлан.docx](Lab1_Жанайдаров_Амирлан.docx) · вариант: software-only (§26 силлабуса)

Виртуальное устройство `room-sensor-01` (замена ESP32 + BME280 + PIR): 4 канала, валидация
значений на устройстве, кодирование JSON или CBOR, кольцевой буфер на 500 записей.
Коллектор на приёмной стороне проверяет сообщения по JSON Schema и контролирует `seq`.

```text
[sensor models] → [sampling] → [validation] → [encoding: JSON|CBOR] → [ring buffer 500] → [MQTT publish]
     device.py      runner.py     device.py         codec.py              device.py         runner.py
                                                                                              │ QoS 1
                                   collector.py ◀── enu/site1/room-sensor-01/up/telemetry/{json|cbor}
                                   JSON Schema · seq gap/dup · ingest_ts → results/collector/<RUN>/
```

## Запуск

```bash
docker compose up -d --build                              # JSON, буфер 500
docker compose logs -f device collector
ENCODING=cbor RUN=cbor docker compose up -d --force-recreate collector device     # CBOR
BUFFER_SIZE=10 RUN=overflow docker compose up -d --force-recreate collector device # тест переполнения
bash tools/outage.sh results/main 90                       # остановить брокер на 90 с
```

Офлайн-инструменты (на хосте, `pip install -r tools/requirements.txt`):

```bash
python tools/size_compare.py --n 2000 --out results/size_compare.json     # JSON vs CBOR
python tools/validate_samples.py --n 20000 --boost 10 --out results/validation.json
python tools/plot_model.py --out report/img/fig1_model.png                 # сутки модели
```

## Структура

| Путь | Назначение |
|---|---|
| `simulator/device.py` | Модели датчиков (суточный цикл, дрейф, AR(1)-шум, сбои), валидатор, сборка сообщения, кольцевой буфер |
| `simulator/codec.py` | JSON, CBOR, компактный CBOR (целые ключи, масштабированные значения) |
| `simulator/runner.py` | Опрос по расписанию + MQTT-uplink с переподключением, удаление из буфера после PUBACK |
| `collector/collector.py` | Декодирование, JSON Schema, контроль seq (gap/dup/late), ingest_ts, CSV и сводка |
| `schema/telemetry.schema.json` | Схема сообщения (draft 2020-12) |
| `tools/` | Сравнение форматов, проверка схемы, график модели, сценарий обрыва |
| `results/` | Журналы, CSV коллектора и сводки по сценариям `main`, `cbor`, `overflow` |

## Результаты

| Проверка | Результат |
|---|---|
| Схема | 20 000 / 20 000 корректных приняты, 10 / 10 неверных отклонены |
| Размер (2000 сообщений) | JSON 189,6 Б · CBOR 162,1 Б (−14,5 %) · компактный CBOR 56,9 Б (−70 %) |
| Трафик одного устройства | JSON 3,28 МБ/сутки · CBOR 2,80 · компактный CBOR 0,98 |
| Обрыв на 90 с | 22 сообщения из буфера досланы, seq 1…61, missing 0, dup 0 |
| Переполнение (буфер 10) | вытеснено 14 (drop_oldest), коллектор: gap(14), missing 14 |
| Автономия буфера 500 | ≈ 42 мин при интервале 5 с |
