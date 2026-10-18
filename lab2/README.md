# Lab 2. Wi-Fi Communication — измерение характеристик канала

Отчёт: [Lab2_Жанайдаров_Амирлан.docx](Lab2_Жанайдаров_Амирлан.docx) · вариант: software-only (§26 силлабуса)

Канал «устройство — точка доступа» эмулируется `tc netem` в обе стороны (eth0 — передача, ifb0 — приём).
Параметры пяти профилей (1, 5, 10, 20, 30 м) выведены из логарифмической модели потерь и таблицы
чувствительности 802.11n HT20: RSSI → MCS → ёмкость канала; потери 0/1/3/10/20 % в оба конца.

```text
[lab2-client] ──eth0: netem ▶──────────────▶ [lab2-mosquitto :1883 / :8883 TLS]
 «устройство» ◀── ifb0: netem ◀── приём ───── [lab2-iperf    :5201]
 ping · iperf3 · mtr · paho-mqtt · TLS-рукопожатие
```

## Запуск

```bash
docker compose up -d --build
docker exec lab2-client python tools/channel_model.py              # таблица профилей
docker exec lab2-client python tools/run_profiles.py --series 3    # ≈ 15 мин, results/raw/
bash tools/nagle_compare.sh                                         # брокер по умолчанию vs set_tcp_nodelay
python tools/analyze.py                                             # таблицы + графики (pip install matplotlib)
docker exec lab2-client python tools/run_profiles.py --apply d20   # включить профиль вручную
docker exec lab2-client python tools/run_profiles.py --clear
```

## Структура

| Путь | Назначение |
|---|---|
| `tools/channel_model.py` | Модель канала: RSSI(d), выбор MCS, параметры netem |
| `tools/run_profiles.py` | netem в обе стороны → ping 200 → iperf3 5 с → MQTT QoS 0/1/2 по 1000 → 90 подключений |
| `tools/analyze.py` | Среднее ± 95 % ДИ, p50/p95/p99, энергобюджет, графики |
| `tools/nagle_compare.sh` | Задержка CONNACK после TLS 1.3 при настройке брокера по умолчанию и с `set_tcp_nodelay` |
| `mosquitto/config/` | `mosquitto.conf` (1883 + 8883 TLS, `set_tcp_nodelay true`) и `mosquitto-nagle.conf` |
| `results/raw/` | Сырые измерения: 3 серии × 5 профилей; `results/summary.json` — сводка |

## Результаты

| RSSI, дБм | Throughput, Мбит/с | Потери ping, % | RTT p50, мс | p99 QoS 1, мс | Подключение TLS 1.3, мс (p50) |
|---|---|---|---|---|---|
| −35 (1 м) | 30,89 ± 0,03 | 0 | 2,4 | 4,7 | 10,9 |
| −58 (5 м) | 30,12 ± 0,96 | 0,8 | 4,2 | 62,9 | 19,3 |
| −68 (10 м) | 10,10 ± 2,62 | 2,0 | 8,6 | 192 | 38,2 |
| −78 (20 м) | 2,02 ± 0,60 | 11,5 | 17,0 | 442 | 81,5 |
| −84 (30 м) | 0,69 ± 0,31 | 21,2 | 30,3 | 3736 | 174 |

- MQTT доставил 100 % сообщений при любом QoS (TCP), но на 30 м QoS 1/2 упираются в окно in-flight:
  в пределах 1 с доставлено 20,9 % (QoS 1) и 3,1 % (QoS 2) против 99,8 % у QoS 0.
- Mosquitto по умолчанию добавляет к TLS 1.3 ≈ 45 мс (Нейгл + NewSessionTicket); `set_tcp_nodelay true` устраняет.
- NFR выполняются при RSSI ≥ −78 дБм; рекомендованный порог покрытия −75 дБм.
