#!/bin/sh
# Обрыв WAN шлюза: wan down — 100 % потерь на пути к облачному брокеру (172.28.2.2), тихо, без RST;
# wan up — снять. Остальной трафик интерфейса (управление шлюзом, /stats) не затрагивается.
IF=$(ip -o -4 addr show | awk '$4 ~ /^172\.28\.2\./ {print $2}')
case "$1" in
  down)
    tc qdisc replace dev "$IF" root handle 1: prio
    tc qdisc add dev "$IF" parent 1:3 handle 30: netem loss 100%
    tc filter add dev "$IF" parent 1:0 protocol ip u32 match ip dst 172.28.2.2/32 flowid 1:3
    echo "WAN $IF: down (loss 100% → 172.28.2.2)" ;;
  up)   tc qdisc del dev "$IF" root 2>/dev/null; echo "WAN $IF: up" ;;
  *)    tc qdisc show dev "$IF"; tc filter show dev "$IF" ;;
esac
