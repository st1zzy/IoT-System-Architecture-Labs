"""Build Lab 7 terminal-style figures from captured console output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from term_render import main as render  # noqa: E402

LAB = Path(sys.argv[1])
C, IMG = LAB / "results" / "console", LAB / "report" / "img"
P = "st1zzy@laptop MINGW64 .../IoT-System-Architecture-Labs/lab7 $"


def fig(name, blocks, cols=150):
    render({"prompt": P, "cols": cols, "blocks": blocks}, str(IMG / f"{name}.png"))


fig("term_deadband", [{"cmd": "python tools/deadband_analysis.py", "file": C / "deadband.txt"}], cols=210)
fig("term_main", [{"cmd": "python tools/run_experiments.py deadband main", "file": C / "exp_main.txt"}], cols=130)
fig("term_rest", [{"cmd": "python tools/run_experiments.py limited_raw unlimited sendmode crash overflow",
                   "file": C / "exp_rest.txt"}], cols=130)
fig("term_crash", [{"cmd": "# отключение питания шлюза во время обрыва WAN", "file": C / "crash_demo.txt"}], cols=130)
