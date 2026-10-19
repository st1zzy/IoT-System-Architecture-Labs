# Lab 5. Telemetry Pipeline

Отчёт: [Lab5_Жанайдаров_Амирлан.docx](Lab5_Жанайдаров_Амирлан.docx) · вариант: software-only

```text
[loadgen × N] ──MQTT QoS 1──▶ [mosquitto] ──▶ [ingest: JSON Schema → enrich → batch 500 | 2 s → SQLite]
                                                  │ /metrics /stats :8000 ──▶ [prometheus :9090]
```

## Запуск

```bash
docker compose up -d --build
python tools/run_experiments.py     # 11 прогонов: нагрузка, батчинг, backpressure → results/runs.json
python tools/plots.py               # графики (pip install matplotlib)
MAX_BATCH=1 BACKPRESSURE=1 docker compose up -d --force-recreate ingest   # режимы ingest
```

## Результаты

| Показатель | Значение |
|---|---|
| Без потерь | до 2000 сообщ./с, p99 0,25–0,74 с |
| Точка насыщения (батч 500) | ≈ 2900–3300 сообщ./с; узкое место — однопоточный приём (CPU 98–116 %) |
| Перегрузка 4000 сообщ./с | записано 3341 сообщ./с, брокер отбросил 19 784 (16 %) |
| Батч 1 / 50 / 500 при 1000 сообщ./с | очередь 4809 / 950 / 500, p99 4,9 / 0,41 / 0,38 с |
| Backpressure (батч 1, 1500 сообщ./с) | без: очередь 28 703, RAM 100 МБ; с: очередь ≤ 1000, RAM 44 МБ, брокер отбросил 15 173 |
| Трафик | 225,5 Б/сообщение на брокере при расчётных 223 Б |

Прогон `batch-500` в `results/runs.json` испорчен приостановкой хоста (скачок часов на 27 мин) и в отчёте
заменён идентичной конфигурацией `load-1000`.
