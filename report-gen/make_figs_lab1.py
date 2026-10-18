"""Build Lab 1 report figures (terminal-style) from captured console output.

Команды на рисунках — те, что реально выполнялись в Git Bash (сценарий прогона).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from term_render import main as render  # noqa: E402

LAB = Path(sys.argv[1])
C, IMG = LAB / "results" / "console", LAB / "report" / "img"
IMG.mkdir(parents=True, exist_ok=True)
P = "st1zzy@laptop MINGW64 .../IoT-System-Architecture-Labs/lab1 $"
LOGS = "docker compose logs --no-log-prefix"


def lines(name: str) -> list[str]:
    return (C / name).read_text(encoding="utf-8").rstrip("\n").split("\n")


def window(name: str, start: str, end: str) -> str:
    ls = lines(name)
    i = next(k for k, s in enumerate(ls) if start in s)
    j = next(k for k in range(i, len(ls)) if end in ls[k])
    return "\n".join(ls[i:j + 1])


def grep(name: str, pattern: str) -> str:
    return "\n".join(s for s in lines(name) if re.search(pattern, s))


def fig(name, blocks, cols=150):
    render({"prompt": P, "cols": cols, "blocks": blocks}, str(IMG / f"{name}.png"))


fig("fig3_device", [
    {"cmd": f"{LOGS} device | head -8", "text": "\n".join(lines("main_device.log")[:8])}])

fig("fig4_collector", [
    {"cmd": f"{LOGS} collector | grep -E 'subscribed|seq=[1-5] |rx=24 '",
     "text": grep("main_collector.log", r"subscribed|seq=[1-5] |rx=24 ")}])

fig("fig5_size_compare", [
    {"cmd": "python tools/size_compare.py --n 2000 --out results/size_compare.json",
     "file": C / "size_compare.txt"}], cols=170)

fig("fig6_cbor", [
    {"cmd": "RUN=cbor ENCODING=cbor docker compose up -d --force-recreate collector device",
     "text": " Container lab1-collector Started\n Container lab1-device Started"},
    {"cmd": f"{LOGS} collector | head -6", "text": "\n".join(lines("cbor_collector.log")[:6])}])

fig("fig7_outage_device", [
    {"cmd": "bash tools/outage.sh results/main 90", "file": LAB / "results" / "main" / "timeline.txt"},
    {"cmd": f"{LOGS} device | sed -n '/05:44:32/,/05:46:32/p'",
     "text": window("main_device.log", "05:44:32", "05:46:32")}])

fig("fig8_outage_collector", [
    {"cmd": f"{LOGS} collector | grep -E '05:46:(2[7-9]|3)|summary.final'",
     "text": grep("main_collector.log", r"05:46:(2[7-9]|3)|summary\(final")}], cols=160)

fig("fig9_overflow", [
    {"cmd": "RUN=overflow BUFFER_SIZE=10 docker compose up -d --force-recreate collector device",
     "text": " Container lab1-collector Started\n Container lab1-device Started"},
    {"cmd": "bash tools/outage.sh results/overflow 90", "file": LAB / "results" / "overflow" / "timeline.txt"},
    {"cmd": f"{LOGS} device | grep -E 'buffer full|connected to|pub seq=2[1-3] '",
     "text": grep("overflow_device.log", r"buffer full|connected to|pub seq=2[1-3] ")},
    {"cmd": f"{LOGS} collector | grep -E 'gap|summary.final'",
     "text": grep("overflow_collector.log", r"gap\(|summary\(final")}], cols=160)

fig("fig2_validation", [
    {"cmd": "python tools/validate_samples.py --n 20000 --boost 10 --out results/validation.json",
     "file": C / "validate_samples.txt"}])
