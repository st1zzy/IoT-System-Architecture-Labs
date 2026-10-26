"""6. Отзыв сертификата dev-003 (с хоста): сеанс, открытый до отзыва + новые подключения каждые 0,25 с.

Хронология: решение → openssl ca -revoke + новая CRL → (5 с) SIGHUP брокеру → (15 с) перезапуск брокера.
    python tools/run_revocation.py     → results/revocation.json
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}


def sh(*cmd: str) -> str:
    return subprocess.run(list(cmd), capture_output=True, text=True, encoding="utf-8", env=ENV).stdout.strip()


def say(m: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    for f in ("probe.jsonl", "hold.jsonl"):
        (RES / f).unlink(missing_ok=True)
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sh("docker", "exec", "-d", "lab9-client", "python", "tools/checks.py", "hold", "75")
    time.sleep(8)
    sh("docker", "exec", "-d", "lab9-client", "python", "tools/checks.py", "probe", "60")
    time.sleep(6)
    ev = {"decision": time.time()}
    say("решение об отзыве dev-003")
    out = sh("docker", "exec", "lab9-client", "sh", "/lab/tools/revoke.sh", "dev-003")
    ev["crl_ready"] = time.time()
    say(f"CRL выпущена за {(ev['crl_ready'] - ev['decision']) * 1000:.0f} мс")
    time.sleep(5)
    sh("docker", "kill", "--signal", "HUP", "lab9-broker")
    ev["sighup"] = time.time()
    say("SIGHUP брокеру (перечитать конфигурацию)")
    time.sleep(15)
    sh("docker", "restart", "lab9-broker")
    ev["restart"] = time.time()
    say("брокер перезапущен")
    time.sleep(45)
    probe = [json.loads(x) for x in (RES / "probe.jsonl").read_text().splitlines()]
    hold = [json.loads(x) for x in (RES / "hold.jsonl").read_text().splitlines()]
    crl = sh("docker", "exec", "lab9-client", "openssl", "crl", "-in", "/pki/crl.pem", "-noout", "-text")
    log = sh("docker", "logs", "--since", since, "lab9-broker")
    log += "\n" + subprocess.run(["docker", "logs", "--since", since, "lab9-broker"], capture_output=True, text=True,
                                 encoding="utf-8", env=ENV).stderr

    def phase(t):
        return "до CRL" if t < ev["crl_ready"] else "CRL выпущена" if t < ev["sighup"] else \
            "после SIGHUP" if t < ev["restart"] else "после перезапуска"
    stats = {}
    for p in probe:
        s = stats.setdefault(phase(p["t"]), {"ok": 0, "rejected": 0, "errors": set()})
        s["ok" if p["ok"] else "rejected"] += 1
        if not p["ok"]:
            s["errors"].add(p["error"][:70])
    for s in stats.values():
        s["errors"] = sorted(s["errors"])
    first_reject = next((p["t"] for p in probe if not p["ok"] and p["t"] > ev["crl_ready"]), None)
    acks = {ph: sum(1 for h in hold if h["ev"] == "puback" and phase(h["t"]) == ph) for ph in
            ("до CRL", "CRL выпущена", "после SIGHUP", "после перезапуска")}
    disc = [h for h in hold if h["ev"] == "disconnect"]
    res = {"events": ev, "probe_phases": stats, "first_reject_after_decision_s": first_reject - ev["decision"]
           if first_reject else None, "hold_pubacks": acks, "hold_disconnects": disc[:3],
           "hold_reconnects": sum(1 for h in hold if h["ev"] == "connect"), "revoke_out": out,
           "crl": crl, "broker_log": [ln for ln in log.splitlines() if "dev-003" in ln or "revoked" in ln.lower()
                                      or "OpenSSL" in ln or "Reloading" in ln or "reload" in ln.lower()][:40]}
    (RES / "revocation.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for ph, s in stats.items():
        print(f"  новые подключения {ph}: успешно {s['ok']}, отказ {s['rejected']} {s['errors'][:1]}")
    print(f"  первый отказ через {res['first_reject_after_decision_s']:.1f} с после решения" if first_reject else "  отказов нет")
    print(f"  открытый сеанс dev-003, PUBACK по фазам: {acks}; разрывов {len(disc)}, переподключений {res['hold_reconnects']}")


if __name__ == "__main__":
    main()
