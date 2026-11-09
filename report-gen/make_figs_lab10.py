"""Build Lab 10 terminal-style figures from captured console output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from term_render import main as render  # noqa: E402

LAB = Path(sys.argv[1])
C, IMG = LAB / "results" / "console", LAB / "report" / "img"
P = "st1zzy@laptop MINGW64 .../IoT-System-Architecture-Labs/lab10 $"
X = "python tools/run_experiments.py "


def fig(name, blocks, cols=150):
    render({"prompt": P, "cols": cols, "blocks": blocks}, str(IMG / f"{name}.png"))


fig("term_single", [{"cmd": X + "single   # 1 узел EMQX (1 vCPU), ingest подписан прямо на брокер",
                     "file": C / "single-5k.txt"},
                    {"file": C / "single-10k.txt"},
                    {"cmd": "docker logs lab10-emqx1 | grep -E 'mqueue_is_full|runq_overload|check_oom_shutdown'",
                     "file": C / "single_emqx.log"}], cols=200)
fig("term_cluster", [{"cmd": X + "cluster  # 2 узла + NGINX L4, тот же единственный подписчик",
                      "file": C / "cluster-10k.txt"}], cols=200)
fig("term_kafka", [{"cmd": X + "kafka    # мосты ×4 → Redpanda (8 партиций) → consumer group",
                    "file": C / "kafka-10k-c2.txt"},
                   {"cmd": "docker logs lab10-emqx1 lab10-emqx2 | grep check_oom_shutdown",
                    "file": C / "kafka_emqx.log"}], cols=200)
fig("term_tuned", [{"cmd": X + "tuned    # + лимиты сессий подписчиков: mqueue 10000, inflight 128, mailbox 10000",
                    "file": C / "tuned-10k-c2b.txt"},
                   {"cmd": X + "three    # третий узел EMQX", "file": C / "three-10k-c2b.txt"}], cols=200)
fig("term_drain", [{"cmd": X + "drain    # очередь 3 млн сообщений, consumer group N = 1, 2, 4",
                    "file": C / "drain.txt"}], cols=200)
fig("term_failover", [{"cmd": X + "failover  # kill -9 emqx2 на 20-й секунде публикации",
                       "file": C / "failover-nobuffer.txt"},
                      {"file": C / "failover-buffer.txt"}], cols=200)
fig("term_herd", [{"cmd": X + "herd   # перезапуск NGINX: 10 000 клиентов теряют соединение одновременно",
                   "file": C / "herd-immediate.txt"},
                  {"file": C / "herd-exp_jitter.txt"}, {"file": C / "herd-exp_jitter10.txt"}], cols=200)
