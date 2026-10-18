#!/usr/bin/env bash
# Захват MQTT-обмена на интерфейсе устройства и разбор tshark (внутри lab3-device).
#   docker exec lab3-device bash tools/wire_capture.sh
set -euo pipefail
OUT=/lab/results/wire
mkdir -p "$OUT"
tcpdump -i eth0 -s 0 -U -w "$OUT/mqtt.pcap" 'tcp port 1883' 2> /dev/null &
TD=$!
sleep 1
python tools/wire_demo.py > "$OUT/demo.txt"
sleep 1
kill "$TD"; wait "$TD" 2> /dev/null || true

tshark -r "$OUT/mqtt.pcap" > "$OUT/summary.txt" 2> /dev/null
# полный разбор PUBLISH QoS 1 по уровням: Ethernet / IP / TCP / MQTT
N=$(tshark -r "$OUT/mqtt.pcap" -Y 'mqtt.msgtype == 3 && mqtt.qos == 1' -T fields -e frame.number 2> /dev/null | head -1)
tshark -r "$OUT/mqtt.pcap" -Y "frame.number == $N" -V -O mqtt > "$OUT/publish_qos1.txt" 2> /dev/null
tshark -r "$OUT/mqtt.pcap" -T fields -E separator=';' -e frame.number -e frame.time_relative -e frame.len \
  -e ip.src -e tcp.len -e mqtt.msgtype -e mqtt.qos -e mqtt.len > "$OUT/fields.csv" 2> /dev/null
python tools/wire_analyze.py "$OUT"
