"""Lab 6: схемы, загрузка, компрессия, CAGG, retention, холодный срез в MinIO → results/lab6.json.

    docker exec lab6-client python tools/experiments.py
Данные: 100 устройств × 4 метрики, раз в 10 мин, год до текущего момента.
"""

import io
import json
import os
import statistics as st
import sys
import time

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from minio import Minio

DEVICES, STEP = 100, "10 minutes"
RES: dict = {}

GEN = """
WITH t AS (SELECT generate_series(date_trunc('minute', now()) - interval '365 days',
                                  date_trunc('minute', now()), interval '{step}') AS ts),
     d AS (SELECT n, 'dev-' || lpad(n::text, 3, '0') AS dev FROM generate_series(1, {devices}) n),
     base AS (SELECT ts, dev, n, sin(2 * pi() * extract(epoch FROM ts) / 86400 + n) AS s FROM t, d)
"""
# значения: суточный цикл + шум, разрешение датчика (0,01 °C, 0,1 %RH, 0,01 гПа); 0,1 % выбросов температуры
TEMP = "round((21 + 4 * s + random() * 0.6 + CASE WHEN random() < 0.001 THEN 18 ELSE 0 END)::numeric, 2)::float8"
HUM = "round((45 - 10 * s + random() * 2)::numeric, 1)::float8"
PRES = "round((972 + 1.5 * s + random() * 0.2)::numeric, 2)::float8"
MOT = "(random() < 0.05)::int::float8"

Q = {   # 4 типовых запроса: узкая и широкая схема
    "last": ("SELECT ts, value FROM {t} WHERE device_id = 'dev-042' AND metric_id = 1 ORDER BY ts DESC LIMIT 1",
             "SELECT ts, (payload->>'temperature')::float8 FROM {t} WHERE device_id = 'dev-042' ORDER BY ts DESC LIMIT 1"),
    "day_avg": ("SELECT device_id, avg(value) FROM {t} WHERE metric_id = 1 AND ts > now() - interval '1 day' GROUP BY 1",
                "SELECT device_id, avg((payload->>'temperature')::float8) FROM {t} WHERE ts > now() - interval '1 day' GROUP BY 1"),
    "anomalies_month": ("SELECT device_id, count(*) FROM {t} WHERE metric_id = 1 AND ts > now() - interval '30 days' "
                        "AND value > 32 GROUP BY 1",
                        "SELECT device_id, count(*) FROM {t} WHERE ts > now() - interval '30 days' "
                        "AND (payload->>'temperature')::float8 > 32 GROUP BY 1"),
    "device_year": ("SELECT time_bucket('1 day', ts) d, avg(value) FROM {t} WHERE device_id = 'dev-042' AND metric_id = 1 "
                    "GROUP BY 1 ORDER BY 1",
                    "SELECT time_bucket('1 day', ts) d, avg((payload->>'temperature')::float8) FROM {t} "
                    "WHERE device_id = 'dev-042' GROUP BY 1 ORDER BY 1"),
}


def say(*a) -> None:
    print(*a, flush=True)


def timed(cur, sql, n=5) -> float:
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        cur.execute(sql)
        cur.fetchall()
        ts.append((time.perf_counter() - t) * 1000)
    return st.median(ts)


def size(cur, table) -> int:
    cur.execute(f"SELECT hypertable_size('{table}')")
    return cur.fetchone()[0]


def queries(cur, label) -> dict:
    out = {}
    for name, (narrow, wide) in Q.items():
        out[name] = {"narrow": timed(cur, narrow.format(t="telemetry")), "wide": timed(cur, wide.format(t="telemetry_wide"))}
    RES[f"queries_{label}"] = out
    say(f"   запросы ({label}), медиана 5 запусков, мс:")
    for name, v in out.items():
        say(f"     {name:<16} узкая {v['narrow']:9.1f}   широкая {v['wide']:9.1f}")
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    con = psycopg.connect(os.environ["PG_DSN"], autocommit=True)
    cur = con.cursor()
    cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
    tsv = (cur.fetchone() or [None])[0]
    if not tsv:
        cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    cur.execute("SELECT version(), (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb')")
    pgv, tsv = cur.fetchone()
    say(f"{pgv.split(',')[0]}, TimescaleDB {tsv}")
    for v in ("telemetry_1h", "telemetry_1d"):
        cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {v} CASCADE")
    cur.execute("DROP TABLE IF EXISTS telemetry, telemetry_wide, metrics, devices CASCADE")

    # 1–2. схемы и hypertable
    cur.execute("CREATE TABLE devices (device_id TEXT PRIMARY KEY, site TEXT NOT NULL, meta JSONB)")
    cur.execute("CREATE TABLE metrics (metric_id SMALLINT PRIMARY KEY, name TEXT UNIQUE NOT NULL, unit TEXT)")
    cur.execute("INSERT INTO metrics VALUES (1,'temperature','°C'),(2,'humidity','%RH'),(3,'pressure','hPa'),(4,'motion','bool')")
    cur.execute(f"INSERT INTO devices SELECT 'dev-' || lpad(n::text, 3, '0'), 'site' || (n % 4 + 1), '{{}}' FROM generate_series(1,{DEVICES}) n")
    cur.execute("CREATE TABLE telemetry (ts TIMESTAMPTZ NOT NULL, device_id TEXT NOT NULL, metric_id SMALLINT NOT NULL, "
                "value DOUBLE PRECISION NOT NULL)")
    cur.execute("CREATE TABLE telemetry_wide (ts TIMESTAMPTZ NOT NULL, device_id TEXT NOT NULL, payload JSONB NOT NULL)")
    for t in ("telemetry", "telemetry_wide"):
        cur.execute(f"SELECT create_hypertable('{t}', by_range('ts', INTERVAL '7 days'))")
    say("1-2) схемы: узкая telemetry(ts, device_id, metric_id, value), широкая telemetry_wide(ts, device_id, payload JSONB); "
        "hypertable, chunk 7 суток")

    # 3. загрузка
    t0 = time.perf_counter()
    cur.execute(GEN.format(step=STEP, devices=DEVICES) +
                f"INSERT INTO telemetry SELECT ts, dev, m, CASE m WHEN 1 THEN {TEMP} WHEN 2 THEN {HUM} WHEN 3 THEN {PRES} "
                f"ELSE {MOT} END FROM base, generate_series(1, 4) m ORDER BY ts")
    load_n = time.perf_counter() - t0
    t0 = time.perf_counter()
    cur.execute(GEN.format(step=STEP, devices=DEVICES) +
                f"INSERT INTO telemetry_wide SELECT ts, dev, jsonb_build_object('temperature', {TEMP}, 'humidity', {HUM}, "
                f"'pressure', {PRES}, 'motion', ({MOT})::int::bool) FROM base ORDER BY ts")
    load_w = time.perf_counter() - t0
    for t in ("telemetry", "telemetry_wide"):
        cur.execute(f"CREATE INDEX ON {t} (device_id, ts DESC)")
    cur.execute("ANALYZE telemetry; ANALYZE telemetry_wide")
    cur.execute("SELECT count(*) FROM telemetry"); n_rows = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM telemetry_wide"); w_rows = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM show_chunks('telemetry')"); chunks = cur.fetchone()[0]
    s_n, s_w = size(cur, "telemetry"), size(cur, "telemetry_wide")
    RES["load"] = {"narrow_rows": n_rows, "wide_rows": w_rows, "narrow_s": load_n, "wide_s": load_w,
                   "narrow_bytes": s_n, "wide_bytes": s_w, "chunks": chunks}
    say(f"3) загрузка: узкая {n_rows:,} строк за {load_n:.1f} с ({n_rows / load_n:,.0f} строк/с), {s_n / 2**20:.0f} МБ, "
        f"{s_n / n_rows:.1f} Б/строку; широкая {w_rows:,} строк за {load_w:.1f} с, {s_w / 2**20:.0f} МБ, "
        f"{s_w / w_rows:.1f} Б/строку ({s_w / w_rows / 4:.1f} Б на измерение); чанков {chunks}")
    queries(cur, "uncompressed")

    # 4. компрессия
    comp = {}
    for t in ("telemetry", "telemetry_wide"):
        seg = "device_id, metric_id" if t == "telemetry" else "device_id"
        cur.execute(f"ALTER TABLE {t} SET (timescaledb.compress, timescaledb.compress_segmentby = '{seg}', "
                    f"timescaledb.compress_orderby = 'ts DESC')")
        t0 = time.perf_counter()
        cur.execute(f"SELECT count(compress_chunk(c)) FROM show_chunks('{t}') c")
        dt = time.perf_counter() - t0
        cur.execute(f"SELECT sum(before_compression_total_bytes), sum(after_compression_total_bytes) "
                    f"FROM chunk_compression_stats('{t}')")
        b, a = cur.fetchone()
        comp[t] = {"before": int(b), "after": int(a), "ratio": b / a, "seconds": dt, "segmentby": seg}
        say(f"4) компрессия {t} (segmentby {seg}, orderby ts DESC): {b / 2**20:.0f} → {a / 2**20:.1f} МБ, "
            f"коэффициент {b / a:.1f}×, за {dt:.1f} с")
    RES["compression"] = comp
    cur.execute("ANALYZE telemetry; ANALYZE telemetry_wide")
    queries(cur, "compressed")

    # 5. continuous aggregates
    caggs = {"telemetry_1h": "1 hour", "telemetry_1d": "1 day"}
    cagg = {}
    for v, b in caggs.items():
        cur.execute(f"CREATE MATERIALIZED VIEW {v} WITH (timescaledb.continuous) AS SELECT time_bucket('{b}', ts) AS bucket, "
                    f"device_id, metric_id, avg(value) AS avg_v, min(value) AS min_v, max(value) AS max_v, count(*) AS n "
                    f"FROM telemetry GROUP BY 1, 2, 3 WITH NO DATA")
        t0 = time.perf_counter()
        cur.execute(f"CALL refresh_continuous_aggregate('{v}', NULL, NULL)")
        dt = time.perf_counter() - t0
        cur.execute(f"SELECT count(*) FROM {v}"); rows = cur.fetchone()[0]
        cur.execute(f"SELECT hypertable_size(format('%I.%I', materialization_hypertable_schema, "
                    f"materialization_hypertable_name)::regclass) FROM timescaledb_information.continuous_aggregates "
                    f"WHERE view_name = '{v}'")
        sz = cur.fetchone()[0]
        cur.execute(f"SELECT add_continuous_aggregate_policy('{v}', start_offset => INTERVAL '3 {b.split()[1]}s', "
                    f"end_offset => INTERVAL '1 {b.split()[1]}', schedule_interval => INTERVAL '{b}')")
        cagg[v] = {"bucket": b, "rows": rows, "bytes": sz, "refresh_s": dt}
        say(f"5) CAGG {v}: {rows:,} строк, {sz / 2**20:.1f} МБ, построение {dt:.1f} с; политика обновления добавлена")
    cq = {
        "day_avg": ("SELECT device_id, avg(value) FROM telemetry WHERE metric_id = 1 AND ts > now() - interval '1 day' GROUP BY 1",
                    "SELECT device_id, sum(avg_v * n) / sum(n) FROM telemetry_1h WHERE metric_id = 1 "
                    "AND bucket > now() - interval '1 day' GROUP BY 1"),
        "device_year": (Q["device_year"][0].format(t="telemetry"),
                        "SELECT bucket, avg_v FROM telemetry_1d WHERE device_id = 'dev-042' AND metric_id = 1 ORDER BY 1"),
        "fleet_year_daily": ("SELECT time_bucket('1 day', ts), avg(value) FROM telemetry WHERE metric_id = 1 GROUP BY 1",
                             "SELECT bucket, sum(avg_v * n) / sum(n) FROM telemetry_1d WHERE metric_id = 1 GROUP BY 1"),
    }
    cres = {k: {"raw": timed(cur, a, 3), "cagg": timed(cur, b, 3)} for k, (a, b) in cq.items()}
    cagg["queries"] = cres
    RES["cagg"] = cagg
    say("   запросы: сырые данные (сжатые) против CAGG, мс:")
    for k, v in cres.items():
        say(f"     {k:<18} сырые {v['raw']:9.1f}   CAGG {v['cagg']:7.1f}   ускорение {v['raw'] / v['cagg']:.0f}×")

    # 7. холодный срез в MinIO (до retention: экспортируем то, что будет удалено)
    cur.execute("SELECT ts, device_id, metric_id, value FROM telemetry WHERE ts < now() - interval '300 days' ORDER BY ts")
    rows = cur.fetchall()
    tbl = pa.table({"ts": pa.array([r[0] for r in rows], pa.timestamp("ms", tz="UTC")),
                    "device_id": pa.array([r[1] for r in rows]).dictionary_encode(),
                    "metric_id": pa.array([r[2] for r in rows], pa.int16()),
                    "value": pa.array([r[3] for r in rows], pa.float64())})
    buf = io.BytesIO()
    pq.write_table(tbl, buf, compression="zstd")
    data = buf.getvalue()
    mc = Minio(os.environ["MINIO_ENDPOINT"], access_key=os.environ["MINIO_USER"],
               secret_key=os.environ["MINIO_PASSWORD"], secure=False)
    if not mc.bucket_exists("cold-tier"):
        mc.make_bucket("cold-tier")
    key = "telemetry/older_than_300d.parquet"
    mc.put_object("cold-tier", key, io.BytesIO(data), len(data), content_type="application/vnd.apache.parquet")
    obj = mc.stat_object("cold-tier", key)
    cur.execute("SELECT sum(after_compression_total_bytes) FROM chunk_compression_stats('telemetry') s "
                "JOIN timescaledb_information.chunks c ON c.chunk_name = s.chunk_name "
                "WHERE c.hypertable_name = 'telemetry' AND c.range_end < now() - interval '300 days'")
    pg_cold = int(cur.fetchone()[0] or 0)
    RES["cold"] = {"rows": len(rows), "parquet_bytes": obj.size, "pg_compressed_bytes_full_chunks": pg_cold,
                   "raw_estimate_bytes": len(rows) * RES["load"]["narrow_bytes"] / RES["load"]["narrow_rows"]}
    say(f"7) холодный срез старше 300 суток: {len(rows):,} строк → Parquet (zstd) {obj.size / 2**20:.1f} МБ в MinIO "
        f"s3://cold-tier/{key}; те же данные в Postgres: без сжатия ≈ {RES['cold']['raw_estimate_bytes'] / 2**20:.0f} МБ, "
        f"целые сжатые чанки {pg_cold / 2**20:.1f} МБ")

    # 6. retention
    cur.execute("SELECT count(*) FROM show_chunks('telemetry')"); before = cur.fetchone()[0]
    cur.execute("SELECT min(ts) FROM telemetry"); oldest_before = cur.fetchone()[0]
    cur.execute("SELECT add_retention_policy('telemetry', INTERVAL '300 days')"); job = cur.fetchone()[0]
    cur.execute(f"CALL run_job({job})")
    cur.execute("SELECT count(*) FROM show_chunks('telemetry')"); after = cur.fetchone()[0]
    cur.execute("SELECT min(ts), count(*) FROM telemetry"); oldest_after, n_after = cur.fetchone()
    RES["retention"] = {"job": job, "chunks_before": before, "chunks_after": after,
                        "oldest_before": oldest_before.isoformat(), "oldest_after": oldest_after.isoformat(),
                        "rows_after": n_after}
    say(f"6) retention 300 суток (задание {job}): чанков {before} → {after}, самая старая запись "
        f"{oldest_before:%Y-%m-%d} → {oldest_after:%Y-%m-%d}, строк осталось {n_after:,}")
    cur.execute("SELECT hypertable_size('telemetry'), hypertable_size('telemetry_wide')")
    RES["final_sizes"] = dict(zip(("narrow", "wide"), cur.fetchone()))
    json.dump(RES, open("/lab/results/lab6.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False, default=str)


if __name__ == "__main__":
    main()
