# Lab 6. Time-Series Database

Отчёт: [Lab6_Жанайдаров_Амирлан.docx](Lab6_Жанайдаров_Амирлан.docx) · вариант: software-only

```text
[client: tools/experiments.py] ──▶ [PostgreSQL 16 + TimescaleDB 2.17]
                                       │ hypertable (чанк 7 сут), compression, CAGG 1h/1d, retention 300 сут
                                       ├──▶ [MinIO: s3://cold-tier, Parquet zstd]
                                       └──▶ [Grafana :3000 — дашборд из provisioning]
```

## Запуск

```bash
docker compose up -d --build
docker exec lab6-client python tools/experiments.py     # ≈ 15 мин: 21 млн строк, все шаги → results/lab6.json
python tools/plots.py                                   # графики (pip install matplotlib)
# Grafana: http://localhost:3000/d/lab6-tsdb
```

## Результаты (100 устройств × 4 метрики × 10 мин × 1 год)

| Конфигурация | Объём | Байт/измерение | Устройство за год, мс |
|---|---|---|---|
| Узкая | 1711 МБ | 85,3 | 760 |
| Узкая + компрессия (13,1×) | 130 МБ | 6,5 | 15 |
| Широкая JSONB | 1011 МБ | 50,4 | 120 |
| Широкая + компрессия (12,0×) | 84 МБ | 4,2 | 61 |
| Parquet zstd в MinIO | — | 2,2 | — |

- CAGG: годовой график парка 310 → 29 мс (11×), устройство за год 21 → 4,5 мс (5×).
- Retention 300 суток: чанков 53 → 44.
- Выбор: узкая схема со сжатием — в 1,5–4,6 раза быстрее по запросам к метрике при близком объёме.

Образ MinIO: `bitnamilegacy/minio:2024.10.13` — официальные образы `minio/minio` больше не публикуются на Docker Hub.
