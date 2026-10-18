"""Build Lab 2 terminal-style figures from captured console output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from term_render import main as render  # noqa: E402

LAB = Path(sys.argv[1])
C, IMG = LAB / "results" / "console", LAB / "report" / "img"
P = "st1zzy@laptop MINGW64 .../IoT-System-Architecture-Labs/lab2 $"
X = "docker exec lab2-client"


def fig(name, blocks, cols=150):
    render({"prompt": P, "cols": cols, "blocks": blocks}, str(IMG / f"{name}.png"))


fig("term_netem", [
    {"cmd": f"{X} python tools/run_profiles.py --apply d20", "file": C / "demo_apply.txt"},
    {"cmd": f"{X} sh -c 'tc qdisc show dev eth0; tc qdisc show dev ifb0'", "file": C / "demo_tc.txt"}])
fig("term_tools", [
    {"cmd": f"{X} ping -c 10 -i 0.2 lab2-mosquitto", "file": C / "demo_ping.txt"},
    {"cmd": f"{X} iperf3 -c lab2-iperf -t 5", "file": C / "demo_iperf.txt"},
    {"cmd": f"{X} mtr -rwc 50 lab2-mosquitto", "file": C / "demo_mtr.txt"}])
fig("term_series", [
    {"cmd": f"{X} python tools/run_profiles.py --series 3", "file": C / "run_profiles.txt"}], cols=128)
fig("term_nagle", [
    {"cmd": "bash tools/nagle_compare.sh", "file": C / "nagle_compare.txt"}])
