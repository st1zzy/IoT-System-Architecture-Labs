# Lab 3. MQTT Publisher/Subscriber

Отчёт: [Lab3_Жанайдаров_Амирлан.docx](Lab3_Жанайдаров_Амирлан.docx) · вариант: software-only (§26 силлабуса)

```text
[lab3-device] publisher × N, tc netem, tcpdump/tshark ──▶ [lab3-mosquitto] ──▶ [lab3-backend] subscriber + / #
 «устройство» с ненадёжным каналом                        mosquitto 2.0.20     приёмник QoS, наблюдатель LWT
```

Топики: `enu/site1/{device_id}/up/telemetry/{metric}`, `.../up/event/motion`, `.../status` (retained + LWT),
`.../down/config` (retained), `.../down/cmd` — см. `tools/topics.py`.

## Запуск

```bash
docker compose up -d --build
docker exec lab3-backend python tools/subscriber.py --filter 'enu/+/+/up/telemetry/temperature' --filter 'enu/#' --duration 25 &
docker exec lab3-device  python tools/publisher.py --devices 3 --interval 1 --duration 20 --drop-every 7

bash tools/run_qos.sh;  python tools/analyze_qos.py          # QoS 0/1/2 × потери 0/5/20 %, 3 серии
bash tools/run_dup.sh                                          # провалы связи: дубли QoS 1
bash tools/run_window.sh                                       # QoS 2: окно клиента 20 / 50, MQTT 3.1.1 / 5
docker exec -d lab3-backend python tools/lwt_observer.py --duration 420
docker exec lab3-device python tools/lwt_test.py --keepalive 5 10 20 --repeats 3
docker exec lab3-backend python tools/retained_test.py
docker exec lab3-device bash tools/wire_capture.sh            # tcpdump + tshark, байты на проводе
python tools/plot_qos.py                                       # графики (pip install matplotlib)
```

## Результаты

| Эксперимент | Результат |
|---|---|
| QoS 1/2 при потерях 0–20 % (окно 20) | доставлено 100 %, дублей 0; p99 QoS 1: 6 → 434 → 2847 мс |
| QoS 0 при 20 % потерь | 88,4 % — 347 сообщений потеряно при разрыве соединения |
| Провалы связи, QoS 1 | 100 % доставлено, 6 дублей; у подписчика DUP = 0 — распознавать только по seq |
| QoS 2, окно 50 > max_inflight брокера | подтверждено издателю 1000, доставлено 976; 3 серии при 20 %: 71,7 % |
| LWT, kill -9 | 0,002 с при любом keep-alive (ОС закрывает сокет) |
| LWT, тихий отказ | 7,9 / 16,1 / 32,0 с при keep-alive 5 / 10 / 20 с (≈ 1,6 × keep-alive) |
| Retained | новый подписчик получает за 0,6–0,8 мс; пустой payload удаляет |
| Байты на проводе (payload 55 Б) | QoS 0: 233 Б (23,6 %), QoS 1: 371 Б (14,8 %), QoS 2: 511 Б (10,8 %) |
| Подписка # при 4000 сообщ./с | 120 003 сообщения, 8003 топика, пик 7910 сообщ./с |

Каталоги `results/qos_inflight50/`, `qos_probe/`, `qos_dup_crashed_script/`, `qos_dup_contaminated/` — сохранённые
промежуточные прогоны: окно 50 (в отчёте), пробные прогоны, а также два прогона дублей, испорченные ошибкой
скрипта и оставшимися от неё persistent-сессиями (в отчёт не вошли, сохранены для прозрачности).
