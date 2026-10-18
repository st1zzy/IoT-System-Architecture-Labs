"""Build Lab S1 terminal-style figures from captured console output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from term_render import main as render  # noqa: E402

LAB = Path(sys.argv[1])
C, IMG = LAB / "results" / "console", LAB / "report" / "img"
P = "st1zzy@laptop MINGW64 .../IoT-System-Architecture-Labs/labS1 $"
EXP = (C / "experiments.txt").read_text(encoding="utf-8").split("\n")


def section(start: str, stop: str) -> str:
    i = next(k for k, s in enumerate(EXP) if s.startswith(start))
    j = next(k for k in range(i + 1, len(EXP)) if EXP[k].startswith(stop))
    return "\n".join(EXP[i:j])


def fig(name, blocks, cols=150):
    render({"prompt": P, "cols": cols, "blocks": blocks}, str(IMG / f"{name}.png"))


fig("term_airtime", [{"cmd": "python tools/lorawan.py", "file": C / "lorawan.txt"}])
fig("term_aloha", [{"cmd": "python tools/experiments.py > results/console/experiments.txt", "text": ""},
                   {"cmd": "sed -n '/^1)/,/^2)/p' results/console/experiments.txt | head -n -1",
                    "text": section("1)", "2)")}])
fig("term_capacity", [{"cmd": "sed -n '/^3)/,/^4)/p' results/console/experiments.txt | head -n -1",
                       "text": section("3)", "4)")}])
fig("term_ns", [
    {"cmd": "docker compose --profile tools run -d --name labs1-app sim python tools/app_counter.py --idle 5",
     "text": ""},
    {"cmd": "docker compose --profile tools run --rm sim python tools/ns_bridge.py --n 1000 --hours 1",
     "file": C / "ns_bridge.txt"},
    {"cmd": "docker logs labs1-app", "file": C / "app_counter.txt"}], cols=175)
