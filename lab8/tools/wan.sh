#!/bin/sh
# Эмуляция WAN edge-агента — только трафик с облачным брокером 172.29.2.2:
#   wan delay MS JITTER — задержка MS±JITTER в каждую сторону (egress: netem; ingress: ifb + netem)
#   wan down            — 100 % потерь в сторону облака (тихий обрыв)
#   wan up              — снять всё
CLOUD=172.29.2.2
IF=$(ip -o -4 addr show | awk '$4 ~ /^172\.29\.2\./ {print $2}')
clear() {
  tc qdisc del dev "$IF" root 2>/dev/null; tc qdisc del dev "$IF" ingress 2>/dev/null
  ip link del ifb0 2>/dev/null; true
}
egress() {   # $@ — параметры netem
  tc qdisc add dev "$IF" root handle 1: prio
  tc qdisc add dev "$IF" parent 1:3 handle 30: netem "$@"
  tc filter add dev "$IF" parent 1:0 protocol ip u32 match ip dst $CLOUD/32 flowid 1:3
}
case "$1" in
  delay)
    clear
    egress delay "${2}ms" "${3}ms" distribution normal
    ip link add ifb0 type ifb && ip link set ifb0 up
    tc qdisc add dev "$IF" handle ffff: ingress
    tc filter add dev "$IF" parent ffff: protocol ip u32 match ip src $CLOUD/32 action mirred egress redirect dev ifb0
    tc qdisc add dev ifb0 root netem delay "${2}ms" "${3}ms" distribution normal
    echo "WAN $IF: задержка ${2}±${3} мс в каждую сторону" ;;
  down) clear; egress loss 100%; echo "WAN $IF: down (loss 100% → $CLOUD)" ;;
  up)   clear; echo "WAN $IF: up" ;;
  *)    tc qdisc show dev "$IF"; tc filter show dev "$IF" ;;
esac
