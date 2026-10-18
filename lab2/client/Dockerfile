# Эмулируемое устройство: измерительные утилиты + tc netem на собственном интерфейсе
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends iproute2 iperf3 iputils-ping mtr-tiny openssl \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /lab
CMD ["sleep", "infinity"]
