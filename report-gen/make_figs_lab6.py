"""Build Lab 6 terminal-style figure from captured console output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from term_render import main as render  # noqa: E402

LAB = Path(sys.argv[1])
render({"prompt": "st1zzy@laptop MINGW64 .../IoT-System-Architecture-Labs/lab6 $", "cols": 150,
        "blocks": [{"cmd": "docker exec lab6-client python tools/experiments.py",
                    "file": LAB / "results" / "console" / "experiments.txt"}]},
       str(LAB / "report" / "img" / "term_experiments.png"))
