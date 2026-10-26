"""Build Lab 8 terminal-style figures from captured console output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from term_render import main as render  # noqa: E402

LAB = Path(sys.argv[1])
C, IMG = LAB / "results" / "console", LAB / "report" / "img"
P = "st1zzy@laptop MINGW64 .../IoT-System-Architecture-Labs/lab8 $"


def fig(name, blocks, cols=150):
    render({"prompt": P, "cols": cols, "blocks": blocks}, str(IMG / f"{name}.png"))


fig("term_tune", [{"cmd": "python tools/tune.py --seeds 1 2 3 4 5", "file": C / "tune_short.txt"}], cols=190)
fig("term_runs", [{"cmd": "python tools/run_experiments.py", "file": C / "experiments.txt"}], cols=200)
fig("term_analyze", [{"cmd": "python tools/analyze.py", "file": C / "analyze_short.txt"}], cols=200)
