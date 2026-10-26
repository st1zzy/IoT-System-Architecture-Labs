# Lab 9. IoT Security

Отчёт: [Lab9_Жанайдаров_Амирлан.docx](Lab9_Жанайдаров_Амирлан.docx) · вариант: software-only (OpenSSL PKI)

```text
 [Root CA] ──▶ [Intermediate CA "Devices CA"] ──▶ broker, dev-001..003, svc-ingest   (EC P-256)
                        │ CRL
 [dev-00x] ══mTLS 1.3══▶ [mosquitto :8883] ──ACL по CN (%u)──▶ [svc-ingest]
 [weak :1883] — брокер «до защиты»: без TLS, ACL по client_id (%c)
```

## Запуск

```bash
docker compose up -d --build                     # pki выпускает ключи в ./pki (в git не попадают)
X="docker exec lab9-client python tools"
$X/checks.py pki; $X/checks.py tls; $X/checks.py capture; $X/checks.py cost; $X/checks.py acl
$X/ota.py; $X/replay.py
python tools/run_revocation.py                   # с хоста: отзыв dev-003, SIGHUP, перезапуск брокера
python tools/plots.py --img report/img           # pip install -r tools/requirements.txt
```

## 8 проверок (33 подпроверки — все OK, `results/*.json`)

| № | Проверка | Результат |
|---|---|---|
| 1 | PKI root → intermediate → leaf | цепочки OK, rogue CA (CN=dev-001) — verification failed |
| 2 | TLS 1.3 only | TLSv1.3 / AES-256-GCM; клиент TLS 1.2 — protocol version |
| 3 | mTLS | без сертификата / чужой CA / просроченный — отказ; клиент отвергает MITM |
| 4 | Перехват | без TLS секрет читается в pcap, с TLS — нет (видны только SNI и размеры) |
| 5 | ACL по CN | чужой топик — PUBACK Not authorized; подмена client_id не помогает (на `%c` — помогает) |
| 6 | Отзыв (CRL) | новые подключения — отказ через 5,4 с (после SIGHUP); открытый сеанс — только после перезапуска, 20,7 с |
| 7 | Подписанное OTA | отклонены: 1 бит в образе, правка манифеста, чужой ключ, откат 1.3.0, повтор; 1.4.0 установлена |
| 8 | Replay | открытый брокер — прошла; повтор байтов TLS — нет; прикладной повтор — отклонён по seq |

## Стоимость TLS (медианы из 30 подключений)

| Вариант | RTT 0, мс | RTT 40 мс, мс | Байт на подключение |
|---|---|---|---|
| Без TLS | 1,7 | 82 | 769 |
| TLS 1.2 + mTLS | 4,5 | 168 (2 RTT) | 5 301 |
| TLS 1.3 + mTLS | 5,2 | 127 (1 RTT) | 7 631 |
| TLS 1.3 + mTLS, resumption | 3,5 | 124 | 4 432 |

- TLS 1.3 тяжелее TLS 1.2 по байтам: клиент предлагает X25519MLKEM768 (ClientHello 1546 Б против 312 Б),
  брокер выбирает x25519 — ≈ 1,2 КБ впустую; плюс 2 NewSessionTicket. Только x25519 — 6397 Б.
- На сообщение 64 Б: 156 → 178 Б (+22 Б: заголовок записи и тег AEAD).
