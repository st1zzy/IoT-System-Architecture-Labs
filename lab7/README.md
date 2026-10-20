# Lab 7. IoT Gateway

Отчёт: [Lab7_Жанайдаров_Амирлан.docx](Lab7_Жанайдаров_Амирлан.docx) · вариант: software-only

```text
 сеть south (LAN объекта)                        сеть wan («облако»)
 [modbus-sim :5020] ──Modbus TCP──┐
 [legacy × 10] ─MQTT─▶ [south] ───┼──▶ [gateway gw-01] ─MQTT QoS 1─▶ [north] ──▶ [ingest] ──▶ [TimescaleDB]
                                  │   drivers → normalize → rules → deadband → buffer (SQLite WAL) → publisher
```

## Запуск

```bash
docker compose up -d --build
curl localhost:8081/stats                         # состояние шлюза: буфер, отправлено, подтверждено, вытеснено
docker exec lab7-gateway wan down                 # обрыв WAN (tc netem 100 % к облачному брокеру); wan up — восстановить
python tools/run_experiments.py                   # все эксперименты (~80 мин) → results/*.json
python tools/deadband_analysis.py                 # deadband по сырому потоку → results/deadband.json
python tools/analyze.py                           # метрики циклов и графики (pip install -r tools/requirements.txt)
```

Режимы шлюза (переменные окружения): `BUFFER_MAX` (10000), `BACKFILL_RATE` записей/с (200; 0 — без ограничения),
`DB_MODE` (abs | pct | off), `DB_SCALE`, `ACK_MODE` (puback | send — ошибочный режим для сравнения), `RAW_LOG`.

## Результаты

| Эксперимент | Результат |
|---|---|
| Deadband abs ×1 (14 420 сырых значений) | экономия 91,3 % (живой поток 91,0 %), ошибка ≤ порога; пик перегрева 96,2 → 96,0 °C |
| 3 цикла «обрыв 5 мин → восстановление» | 0 потерь и 0 дублей из 4962; обрыв замечен за 12,6–14,3 с; досылка 6–10 с, пик 200 записей/с |
| Аларм перегрева | при связи 2 мс (обычная запись 0,8 с); после обрыва — первым, до 1000+ записей из буфера |
| Backfill 9 600 записей: лимит 200 / без лимита | 55 с, пик 220 зап./с, CPU БД 12 % / 2 с, пик 5340 зап./с, CPU БД 65 % |
| Удаление при отправке (ACK_MODE=send) | 158 потерь из 4219 за 3 обрыва; по PUBACK — 0 |
| kill -9 шлюза во время обрыва | 0 потерь из 632, буфер восстановлен из файла |
| Буфер 1000, обрыв 5 мин | вытеснено 139 старых записей = 139 пропусков у ingest, аларм сохранён |
| Буфер на 72 ч | ≈ 931 000 записей, ≈ 182 МБ (10 000 записей = 46 мин автономии) |
