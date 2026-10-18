# Lab 0. Развёртывание стенда курса

Отчёт: [Lab0_Жанайдаров_Амирлан.docx](Lab0_Жанайдаров_Амирлан.docx) · вариант: software-only (§26 силлабуса)

Единый стенд курса: MQTT-брокер Eclipse Mosquitto и виртуальное IoT-устройство, которое раз в 5 с
публикует температуру с QoS 1. Устройство разделено на опрос (по расписанию) и отправку
(с переподключением); при недоступности брокера данные копятся в локальном буфере и досылаются
после восстановления связи.

```text
┌──────────────────────────┐   MQTT 3.1.1, QoS 1   ┌───────────────────┐
│ simulator                │──────────────────────▶│ mosquitto         │
│  dev-000000              │  enu/site1/dev-000000 │  :1883 TCP        │
│  sampler ─▶ buffer(500)  │      /up/telemetry    │  :9001 WebSocket  │
│           ─▶ uplink      │      /status (retain) │  persistence: on  │
└──────────────────────────┘                       └─────────┬─────────┘
                                                             │
                                  mosquitto_sub · MQTT Explorer · tools/measure.py
```

## Запуск

```bash
docker compose up -d --build
docker exec mosquitto mosquitto_sub -t "#" -v      # поток сообщений
```

Измерительные скрипты (на хосте):

```bash
python -m venv .venv
.venv/Scripts/pip install -r tools/requirements.txt      # Linux/macOS: .venv/bin/pip
.venv/Scripts/python tools/measure.py --duration 120 --out results/run1.json
bash tools/broker_outage.sh results/outage 30             # PY=.venv/Scripts/python
docker run -d --name mq-nopersist -p 1884:1883 eclipse-mosquitto:2.0.20 mosquitto -c /mosquitto-no-auth.conf
.venv/Scripts/python tools/persistence_demo.py --case mosquitto:1883 --case mq-nopersist:1884
docker rm -f mq-nopersist
```

> В Git Bash на Windows перед `docker exec … -t "#"` и `docker run … -c /mosquitto-no-auth.conf`
> нужен `MSYS_NO_PATHCONV=1`, иначе пути внутри контейнера переписываются в пути Windows.

## Структура

| Путь | Назначение |
|---|---|
| `docker-compose.yml` | Стенд: `mosquitto` + `simulator` |
| `mosquitto/config/mosquitto.conf` | listener 1883/9001, persistence, log_type all |
| `simulator/device.py` | Модель датчика (суточный цикл, AR(1)-шум, дрейф, выбросы) и устройства с буфером |
| `simulator/runner.py` | MQTT-uplink: QoS 1, LWT, переподключение 1→30 с, удаление из буфера после PUBACK |
| `tools/measure.py` | Интервал, размер, задержка, пропуски и дубли `seq` |
| `tools/broker_outage.sh` | Остановка брокера на N секунд во время потока |
| `tools/persistence_demo.py` | Сравнение брокеров с persistence и без неё |
| `results/` | Сырые результаты (JSON, логи, вывод консоли) |
| `report/img/` | Рисунки отчёта |

## Результаты

| Показатель | Значение |
|---|---|
| Интервал (3 × 120 с) | 5,000 с, σ ≈ 1,5 мс; в прогоне 3 выброс до 3 с из-за хоста |
| Размер сообщения | 126 Б payload / 166 Б MQTT PUBLISH / ≈ 274 Б на линии |
| Трафик одного устройства | ≈ 4,7 МБ в сутки |
| Задержка доставки | p50 ≈ 2 мс |
| Остановка брокера на 32 с | 6 сообщений из буфера досланы, потерь 0, дублей 0 |
| Рестарт брокера: persistence on / off | очередь 5 → 5 / 5 → 0; retained сохранён / утерян |
| Ресурсы | ≈ 38 МиБ RAM на весь стенд |

Версии: Docker Engine 29.2.1, Compose 5.0.2, Mosquitto 2.0.20, Python 3.12, aiomqtt 2.3.0, paho-mqtt 2.1.0.
