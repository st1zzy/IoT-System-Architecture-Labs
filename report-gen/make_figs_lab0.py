"""Build Lab 0 report figures from captured console output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from term_render import main as render  # noqa: E402

LAB = Path(sys.argv[1])
R, C, IMG = LAB / "results", LAB / "results" / "console", LAB / "report" / "img"
IMG.mkdir(parents=True, exist_ok=True)
P = r"PS C:\...\IoT-System-Architecture-Labs\lab0>"
only = sys.argv[2:]


def fig(name, blocks, cols=150):
    if not only or name in only:
        render({"prompt": P, "cols": cols, "blocks": blocks}, str(IMG / f"{name}.png"))


fig("fig1_measure", [
    {"cmd": "python tools/measure.py --duration 120 --out results/run1.json", "file": C / "measure_run1.txt"},
])
fig("fig2_mosquitto_sub", [
    {"cmd": 'docker exec mosquitto mosquitto_sub -t "#" -v -C 12', "file": C / "mosquitto_sub.txt"},
], cols=170)
fig("fig3_outage", [
    {"cmd": "bash tools/broker_outage.sh results/outage 30", "file": R / "outage" / "timeline.txt"},
    {"cmd": "Get-Content results/outage/simulator.log", "file": C / "outage_simulator.log"},
    {"cmd": "docker stats --no-stream", "file": C / "docker_stats_after_outage.txt"},
])
fig("fig5_persistence", [
    {"cmd": "python tools/persistence_demo.py --case mosquitto:1883 --case mq-nopersist:1884",
     "file": C / "persistence.txt"},
])
fig("fig4_broker_log", [
    {"cmd": 'Select-String -Path mosquitto/log/mosquitto.log -Pattern "terminating|Saving|starting|dev-000000"',
     "file": R / "broker_log_excerpt.txt"},
], cols=160)
