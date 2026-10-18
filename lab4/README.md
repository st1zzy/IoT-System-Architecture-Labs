# Lab 4. MQTT Broker — конфигурация и возможности

Отчёт: [Lab4_Жанайдаров_Амирлан.docx](Lab4_Жанайдаров_Амирлан.docx) · вариант: software-only

```text
[client] ──▶ [mosquitto-a :1883] ══ bridge ══▶ [mosquitto-b]          [emqx :1883, dashboard :18083]
             пароли · ACL · persistence · лимиты · $SYS
```

## Запуск

```bash
docker compose up -d --build        # secrets генерирует случайные пароли (secrets/, broker-a/config/passwd — не в Git)
bash tools/run_checks.sh            # 8 проверок → results/console/checks.txt, results/checks.json
```

## Конфигурация

- `broker-a/config/mosquitto.conf` — `allow_anonymous false`, password file, ACL, persistence,
  `max_inflight_messages 20`, `max_queued_messages 100`, `message_size_limit 1024`, мост `out`/`both`/`in`.
- `broker-a/config/acl` — устройства по шаблону `%u`, роли `gateway01`, `svc-ingest`, `dashboard`.

## Результаты: 8 из 8 проверок пройдено

| № | Проверка | Результат |
|---|---|---|
| 1 | Аутентификация | анонимно и с неверным паролем — Not authorized; 4 пользователя подключились |
| 2 | ACL | чужой топик: MQTT 3.1.1 — PUBACK, но не доставлено (молча); MQTT 5 — PUBACK 135 Not authorized |
| 3 | Persistence | после рестарта session_present = 1, 20 из 20 сообщений |
| 4 | Лимиты | 2048 Б отброшено; офлайн-клиенту из 150 доставлено 100, dropped = 50 |
| 5 | Bridge | каждая копия ровно одна в A и B — петель нет |
| 6 | $SYS | 52 метрики, состояние моста = 1 |
| 7 | EMQX 5.8.0 | узел запущен, 4 слушателя |
| 8 | Shared subscription | 1000 / 1000 / 1000 из 3000 на Mosquitto и EMQX |
