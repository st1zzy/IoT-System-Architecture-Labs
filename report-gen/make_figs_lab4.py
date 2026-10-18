"""Build Lab 4 terminal-style figures from captured console output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from term_render import main as render  # noqa: E402

LAB = Path(sys.argv[1])
C, IMG = LAB / "results" / "console", LAB / "report" / "img"
IMG.mkdir(parents=True, exist_ok=True)
P = "st1zzy@laptop MINGW64 .../IoT-System-Architecture-Labs/lab4 $"
text = (C / "checks.txt").read_text(encoding="utf-8").split("\n")
cut = next(i for i, s in enumerate(text) if s.startswith("[ПРОЙДЕНО] 5."))


def fig(name, blocks, cols=150):
    render({"prompt": P, "cols": cols, "blocks": blocks}, str(IMG / f"{name}.png"))


fig("term_checks_1", [{"cmd": "bash tools/run_checks.sh", "text": "\n".join(text[:cut])}], cols=160)
fig("term_checks_2", [{"cmd": "# продолжение вывода", "text": "\n".join(text[cut:])}], cols=160)
fig("term_broker", [{"cmd": "docker logs lab4-mosquitto-a | grep -E 'not authorised|dropped|bridge|Saving'",
                     "file": C / "broker_a_events.txt"},
                    {"cmd": "docker exec lab4-emqx sh -c 'emqx ctl status; emqx ctl cluster status; emqx ctl listeners'",
                     "file": C / "emqx.txt"}], cols=130)
