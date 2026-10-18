"""tc netem на канале «устройство — брокер» в обе стороны: eth0 (передача) и ifb0 (приём)."""

import subprocess


def sh(cmd: str, check: bool = True) -> None:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{cmd}: {r.stderr.strip()}")


def clear() -> None:
    sh("tc qdisc del dev eth0 root", check=False)
    sh("tc qdisc del dev eth0 ingress", check=False)
    sh("ip link del ifb0", check=False)


def apply(loss_pct: float, delay_ms: float = 5) -> None:
    """Потери loss_pct на каждое направление и базовая задержка delay_ms в одну сторону."""
    clear()
    spec = f"netem delay {delay_ms}ms loss {loss_pct}%"
    sh(f"tc qdisc add dev eth0 root {spec}")
    sh("ip link add ifb0 type ifb && ip link set ifb0 up")
    sh("tc qdisc add dev eth0 handle ffff: ingress")
    sh("tc filter add dev eth0 parent ffff: protocol all u32 match u32 0 0 action mirred egress redirect dev ifb0")
    sh(f"tc qdisc add dev ifb0 root {spec}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] != "off":
        apply(float(sys.argv[1]))
        print(f"netem: потери {sys.argv[1]} % в каждую сторону, задержка 5 мс")
    else:
        clear()
        print("netem: off")
