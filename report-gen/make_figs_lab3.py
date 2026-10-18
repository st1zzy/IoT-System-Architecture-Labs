"""Build Lab 3 terminal-style figures from captured console output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from term_render import main as render  # noqa: E402

LAB = Path(sys.argv[1])
C, W, IMG = LAB / "results" / "console", LAB / "results" / "wire", LAB / "report" / "img"
P = "st1zzy@laptop MINGW64 .../IoT-System-Architecture-Labs/lab3 $"
DEV, BE = "docker exec lab3-device", "docker exec lab3-backend"


def fig(name, blocks, cols=150):
    render({"prompt": P, "cols": cols, "blocks": blocks}, str(IMG / f"{name}.png"))


fig("term_pubsub", [
    {"cmd": f"{BE} python tools/subscriber.py --filter 'enu/+/+/up/telemetry/temperature' --filter 'enu/#' "
            f"--duration 25 --show 2 &", "text": ""},
    {"cmd": f"{DEV} python tools/publisher.py --devices 3 --interval 1 --duration 20 --drop-every 7",
     "file": C / "pubsub_pub.txt"},
    {"cmd": "# вывод подписчика", "file": C / "pubsub_sub.txt"}], cols=140)
fig("term_qos", [{"cmd": "bash tools/run_qos.sh > results/console/qos.txt; python tools/analyze_qos.py",
                  "file": C / "qos_summary.txt"}], cols=175)
fig("term_window", [{"cmd": "bash tools/run_window.sh", "file": C / "window.txt"}], cols=150)
fig("term_dup", [{"cmd": "bash tools/run_dup.sh | grep -E '^(pub|sink)'", "file": C / "dup_lines.txt"}], cols=150)
fig("term_lwt", [{"cmd": f"{DEV} python tools/lwt_test.py --keepalive 5 10 20 --repeats 3", "file": C / "lwt.txt"},
                 {"cmd": "docker compose logs --no-log-prefix mosquitto | grep -E 'lwt-k(5|20)-(kill|silent)-1'",
                  "file": C / "lwt_broker.log"}], cols=130)
fig("term_retained", [{"cmd": f"{BE} python tools/retained_test.py", "file": C / "retained.txt"}], cols=130)
fig("term_wire", [{"cmd": f"{DEV} bash tools/wire_capture.sh", "file": C / "wire.txt"},
                  {"cmd": f"{DEV} tshark -r results/wire/mqtt.pcap", "file": W / "summary.txt"}], cols=175)
fig("term_tshark", [{"cmd": f"{DEV} tshark -r results/wire/mqtt.pcap -Y 'frame.number == 10' -V -O mqtt",
                     "file": W / "publish_qos1.txt"}], cols=150)
fig("term_hash", [
    {"cmd": f"{BE} python tools/hash_load.py sub --filter 'enu/#' --duration 40 &", "text": ""},
    {"cmd": f"{BE} python tools/hash_load.py sub --filter 'enu/site1/dev-00042/#' --duration 40 &", "text": ""},
    {"cmd": f"{DEV} python tools/hash_load.py gen --devices 2000 --interval 2 --duration 30", "file": C / "hash_gen.txt"},
    {"cmd": "# вывод подписчиков", "file": C / "hash_sub.txt"}], cols=150)
