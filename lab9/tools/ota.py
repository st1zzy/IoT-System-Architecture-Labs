"""Подписанное OTA: сборка и подпись образа (openssl dgst, ключ подписи прошивок отдельный от TLS PKI)
и проверка на устройстве: подпись манифеста → SHA-256 образа → тип устройства → версия > текущей.

    python tools/ota.py      # 6 сценариев → results/ota.json
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

OTA = Path("/lab/ota")
KEYS, OUT = OTA / "keys", OTA / "out"
RESULT = []


def sh(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True, capture_output=True)


def vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def build(name: str, version: str, key: str = "fw_signing") -> Path:
    """Образ 256 КиБ + манифест {type, version, size, sha256}; подпись ECDSA P-256 / SHA-256 по манифесту."""
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    img = os.urandom(256 * 1024)
    (d / "firmware.bin").write_bytes(img)
    man = {"device_type": "env-node", "version": version, "size": len(img), "sha256": hashlib.sha256(img).hexdigest(),
           "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (d / "manifest.json").write_text(json.dumps(man, separators=(",", ":")), encoding="utf-8")
    sh(f"openssl dgst -sha256 -sign {KEYS}/{key}.key -out {d}/manifest.sig {d}/manifest.json")
    return d


class Device:
    """Сторона устройства: доверяет только встроенному открытому ключу; версия хранится как монотонный счётчик."""

    def __init__(self, version: str):
        self.version = version
        self.pub = serialization.load_pem_public_key((KEYS / "fw_signing.pub").read_bytes())

    def install(self, d: Path) -> tuple[bool, str]:
        raw = (d / "manifest.json").read_bytes()
        try:
            self.pub.verify((d / "manifest.sig").read_bytes(), raw, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:
            return False, "подпись манифеста неверна"
        man = json.loads(raw)
        img = (d / "firmware.bin").read_bytes()
        if len(img) != man["size"] or hashlib.sha256(img).hexdigest() != man["sha256"]:
            return False, "SHA-256 образа не совпадает с подписанным манифестом"
        if man["device_type"] != "env-node":
            return False, "образ для другого типа устройства"
        if vtuple(man["version"]) <= vtuple(self.version):
            return False, f"версия {man['version']} не новее установленной {self.version} (anti-rollback)"
        self.version = man["version"]
        return True, f"установлена версия {man['version']}"


def case(name: str, expect_ok: bool, dev: Device, d: Path) -> None:
    before = dev.version
    t = time.perf_counter()
    ok, why = dev.install(d)
    ms = (time.perf_counter() - t) * 1000
    RESULT.append({"case": name, "expected": "принят" if expect_ok else "отклонён", "accepted": ok, "reason": why,
                   "version_before": before, "version_after": dev.version, "verify_ms": ms, "pass": ok == expect_ok})
    print(f"  [{'OK' if ok == expect_ok else 'FAIL'}] {name}: {'принят' if ok else 'отклонён'} — {why} "
          f"(версия {before} → {dev.version}, проверка {ms:.1f} мс)", flush=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    shutil.rmtree(OUT, ignore_errors=True)
    KEYS.mkdir(parents=True, exist_ok=True)
    for k in ("fw_signing", "attacker"):
        if not (KEYS / f"{k}.key").exists():
            sh(f"openssl ecparam -name prime256v1 -genkey -noout -out {KEYS}/{k}.key && chmod 600 {KEYS}/{k}.key")
    sh(f"openssl ec -in {KEYS}/fw_signing.key -pubout -out {KEYS}/fw_signing.pub")
    print("7. Подписанное OTA (устройство на версии 1.3.2)")
    dev = Device("1.3.2")
    good = build("v1.4.0", "1.4.0")
    tampered = build("v1.4.0-tampered", "1.4.0")
    b = bytearray((tampered / "firmware.bin").read_bytes())
    b[1000] ^= 0x01                                           # один бит в образе после подписи
    (tampered / "firmware.bin").write_bytes(bytes(b))
    edited = build("v9.9.9-edited", "1.4.0")
    m = json.loads((edited / "manifest.json").read_text())
    m["version"] = "9.9.9"                                    # манифест изменён без новой подписи
    (edited / "manifest.json").write_text(json.dumps(m, separators=(",", ":")))
    forged = build("v1.5.0-forged", "1.5.0", key="attacker")
    old = build("v1.3.0-old", "1.3.0")                         # законно подписанная старая (уязвимая) версия
    case("(а) образ изменён на 1 бит после подписи", False, dev, tampered)
    case("(б) манифест изменён (версия 9.9.9) без подписи", False, dev, edited)
    case("(в) подпись чужим ключом", False, dev, forged)
    case("(г) откат на старую версию 1.3.0 с верной подписью", False, dev, old)
    case("(д) корректное обновление 1.4.0", True, dev, good)
    case("(е) повторная установка того же пакета 1.4.0", False, dev, good)
    Path("/lab/results/ota.json").write_text(json.dumps({"checks": RESULT}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
