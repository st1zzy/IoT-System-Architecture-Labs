"""Build Lab 9 terminal-style figures from captured console output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from term_render import main as render  # noqa: E402

LAB = Path(sys.argv[1])
C, IMG = LAB / "results" / "console", LAB / "report" / "img"
P = "st1zzy@laptop MINGW64 .../IoT-System-Architecture-Labs/lab9 $"
X = "docker exec lab9-client python tools/"


def fig(name, blocks, cols=150):
    render({"prompt": P, "cols": cols, "blocks": blocks}, str(IMG / f"{name}.png"))


fig("term_pki_tls", [{"cmd": X + "checks.py pki", "file": C / "check_pki.txt"},
                     {"cmd": X + "checks.py tls", "file": C / "check_tls.txt"}], cols=175)
fig("term_acl", [{"cmd": X + "checks.py acl", "file": C / "check_acl.txt"}], cols=175)
fig("term_capture", [{"cmd": X + "checks.py capture", "file": C / "check_capture.txt"},
                     {"cmd": "tshark -r results/capture.pcap -Y 'tcp.port==1883 && mqtt'", "file": C / "capture_plain.txt"},
                     {"cmd": "tshark -r results/capture.pcap -Y 'tcp.port==1883 && mqtt.msgtype==3' -x | head -14",
                      "file": C / "capture_plain_hex.txt"},
                     {"cmd": "tshark -r results/capture.pcap -Y 'tcp.port==8883 && tls'", "file": C / "capture_tls.txt"}],
    cols=165)
fig("term_cost", [{"cmd": X + "checks.py cost", "file": C / "check_cost.txt"},
                  {"cmd": "# группы обмена ключами в ClientHello TLS 1.3", "file": C / "check_groups.txt"}], cols=190)
fig("term_ota", [{"cmd": X + "ota.py", "file": C / "check_ota.txt"}], cols=175)
fig("term_replay", [{"cmd": X + "replay.py", "file": C / "check_replay.txt"},
                    {"cmd": "docker logs lab9-broker | grep -E 'OpenSSL|Protocol error'", "file": C / "replay_broker.log"}],
    cols=190)
fig("term_revocation", [{"cmd": "python tools/run_revocation.py", "file": C / "check_revocation.txt"},
                        {"cmd": "docker logs lab9-broker   # около SIGHUP и перезапуска", "file": C / "revocation_broker.log"}],
    cols=165)
