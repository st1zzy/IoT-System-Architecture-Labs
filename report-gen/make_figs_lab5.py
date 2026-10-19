"""Build Lab 5 terminal-style figures from captured console output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from term_render import main as render  # noqa: E402

LAB = Path(sys.argv[1])
C, IMG = LAB / "results" / "console", LAB / "report" / "img"
P = "st1zzy@laptop MINGW64 .../IoT-System-Architecture-Labs/lab5 $"


def fig(name, blocks, cols=150):
    render({"prompt": P, "cols": cols, "blocks": blocks}, str(IMG / f"{name}.png"))


fig("term_runs", [{"cmd": "python tools/run_experiments.py", "file": C / "experiments.txt"}], cols=135)
fig("term_metrics", [{"cmd": "curl -s localhost:8000/metrics | grep -E '^messages_|^queue_depth|batch_size|latency'",
                      "file": C / "metrics.txt"},
                     {"cmd": "curl -s 'localhost:9090/api/v1/query?query=messages_stored_total'",
                      "file": C / "prometheus.json"}], cols=150)
