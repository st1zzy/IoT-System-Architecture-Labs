# Lab 10. Scalable IoT Architecture

Отчёт: [Lab10_Жанайдаров_Амирлан.docx](Lab10_Жанайдаров_Амирлан.docx) · вариант: software-only, один ноутбук; «VM» = контейнер с 1 vCPU

```text
[loadgen: 1 000–20 000 MQTT-клиентов, QoS 1, 0,2 сообщения/с]
            │
      [NGINX L4, least_conn]
       ├──▶ [emqx1] ─┐
       ├──▶ [emqx2] ─┤ кластер EMQX 5.8 (1 vCPU на узел)
       └──▶ [emqx3] ─┘  (этап three)
            │ $share/bridge/telemetry/#
      [bridge ×4–6] ──▶ [Redpanda: telemetry, 8 партиций, ключ = device_id]
                                 │ consumer group "ingest"
                          [ingest × N] ──▶ [TimescaleDB]
      [Prometheus → Grafana]
```

## Запуск

```bash
docker compose up -d --build emqx1 timescale loadgen prometheus grafana
# серия экспериментов (с хоста, только stdlib Python):
python tools/run_experiments.py single cluster kafka     # базовые прогоны, лимиты EMQX по умолчанию
python tools/run_experiments.py tuned drain herd three   # + лимиты сессий, consumer group, herd, 3 узла
python tools/run_experiments.py failover                 # отказ узла (запускался после tuned)
python tools/plots.py --img report/img                   # pip install -r tools/requirements.txt
```

Каждый прогон → `results/runs/<run>.json` (+ `<run>_loadgen.json`) и `results/console/<run>.txt`;
сводка — `results/summary.json`; журналы EMQX — `results/emqx-logs/`.

## Результаты (10 000 клиентов = 2 000 сообщений/с)

| Конфигурация | Клиентов | Записано в БД | p99, мс | CPU EMQX, % |
|---|---|---|---|---|
| 1 узел, ingest подписан на брокер | 1 000 | 100 % | 111 | 15 |
| 1 узел | 5 000 | 89,4 % | 4 172 | 69 |
| 1 узел | 10 000 | 7,7 % | 7 502 | 103 |
| 2 узла + NGINX | 10 000 | 43,2 % | 3 646 | 100/88 |
| 2 узла + Kafka (мосты, consumer group) | 10 000 | 92,2 % | 1 516 | 87/85 |
| 2 узла + Kafka + лимиты сессий, 3 повтора | 10 000 | 100 % | 2 138 / 10 705 / 263 | 71–101 |
| 3 узла + Kafka + лимиты, 2 повтора | 10 000 | 100 % | 326 / 300 | 55–65 |
| 3 узла + Kafka + лимиты | 20 000 | 100 % | 8 975 | 96 |

- Первым исчерпан не CPU и не дескрипторы, а **единственная сессия подписчика**: очередь 1 000 →
  `queue_full`, затем `mailbox_overflow` → брокер рвёт соединение → `no_subscribers`.
- Со стандартными лимитами EMQX выбивал и мосты; сообщения из их очередей **не видны в метриках** —
  только в сквозной сверке seq.
- NFR (0 потерь после PUBACK, p99 ≤ 1 с) при удвоенной нагрузке 10 000 выполнен на 3 узлах.
- Consumer group (3 млн в очереди): 52,8k → 63,3k → 88,9k сообщ./с при N = 1, 2, 4 — упор в общую БД.
- Отказ узла (5 000 клиентов): без буфера 960 потерь на устройствах, 0 после PUBACK, 99 % вернулись за 2,9 с;
  с буфером — 13 потерь подтверждённых, но не доставленных сообщений (сессии не реплицируются).
- Thundering herd (10 000): «сразу» — 3 009 переподключились за 85 с; без джиттера — 8 997;
  full jitter 1·2ⁿ — 9 976; full jitter 10·2ⁿ — 9 994, пик попыток в 10 раз ниже (1 360/с против 13 640/с).

## Поправки стенда (найдены при калибровке)

- `ERL_FLAGS="+S 1:1 …"` — иначе 16 планировщиков Erlang с активным ожиданием сжигают квоту 1 vCPU.
- Healthcheck — проверка TCP-портов, не `emqx ctl status` (каждые 5 с поднимал отдельную Erlang VM).
- Перед каждым прогоном — перезапуск контейнера генератора и ожидание пустых простаивающих брокеров.
- 0,2 сообщения/с на устройство: при 1 сообщении/с весь стенд упирался в CPU ноутбука.
