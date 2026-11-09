-- Хранилище телеметрии Lab 10: одна гипертаблица, время = момент записи в БД
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE telemetry (
    stored_at  timestamptz NOT NULL DEFAULT now(),   -- начало транзакции пакета ≈ момент записи
    run        text        NOT NULL,
    device_id  text        NOT NULL,
    seq        integer     NOT NULL,
    ts_ms      bigint      NOT NULL,                 -- время измерения на устройстве
    recv_ms    bigint      NOT NULL,                 -- время получения consumer'ом
    kafka_ms   bigint,                               -- метка Kafka: момент produce() в мосте (NULL без Kafka)
    temp       real,
    hum        real,
    bat        real,
    site       text
);
SELECT create_hypertable('telemetry', by_range('stored_at', INTERVAL '1 hour'));
