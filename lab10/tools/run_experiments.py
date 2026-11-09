"""Оркестратор экспериментов Lab 10 (запуск с хоста: uv run --no-project python tools/run_experiments.py <этап>).

Этапы:
  single    — 1 узел EMQX, ingest подписан прямо на брокер; 1k / 5k / 10k клиентов
  cluster   — 2 узла EMQX + NGINX L4, тот же ingest; 10k / 20k
  kafka     — + мосты ×2 → Redpanda (8 партиций) → ingest × N (consumer group); 10k / 20k × N
  drain     — пропускная способность consumer group: накопить 600k сообщений, разобрать N = 1, 2, 4
  failover  — 10k клиентов, kill узла emqx2 под нагрузкой; без буфера и с буфером на устройстве
  herd      — массовый разрыв (перезапуск NGINX) при трёх политиках переподключения
Каждый прогон → results/runs/<run>.json (+ <run>_loadgen.json от генератора) и results/console/<run>.txt.
"""
import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(LAB, "results")
RUNS = os.path.join(RES, "runs")
CON = os.path.join(RES, "console")
os.makedirs(RUNS, exist_ok=True)
os.makedirs(CON, exist_ok=True)
ENV = dict(os.environ, MSYS_NO_PATHCONV="1")
AUTH = "Basic " + base64.b64encode(b"lab10:lab10-api-secret-not-for-production").decode()


def sh(*cmd, check=True, capture=True, timeout=None):
    r = subprocess.run(cmd, cwd=LAB, env=ENV, capture_output=capture, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} → {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout if capture else ""


def compose(*args, env_extra=None):
    env = dict(ENV, **(env_extra or {}))
    r = subprocess.run(["docker", "compose", *args], cwd=LAB, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"compose {args}: {r.stderr}")


def psql(sql):
    return sh("docker", "exec", "lab10-timescale", "psql", "-U", "postgres", "-d", "iot", "-At", "-F", "|", "-c", sql)


def api(path, port=18083):
    req = urllib.request.Request(f"http://localhost:{port}/api/v5/{path}", headers={"Authorization": AUTH})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def wait_healthy(name, timeout=180):
    t = time.time()
    while time.time() - t < timeout:
        s = sh("docker", "inspect", "-f", "{{.State.Health.Status}}", name, check=False).strip()
        if s == "healthy":
            return
        time.sleep(2)
    raise RuntimeError(f"{name} not healthy")


def emqx_nodes():
    try:
        return {n["node"].split("@")[1].split(".")[0]: n for n in api("nodes")}
    except Exception:
        return {}


def wait_cluster(n):
    t = time.time()
    while time.time() - t < 180:
        nodes = emqx_nodes()
        if sum(1 for v in nodes.values() if v.get("node_status") == "running") >= n:
            return
        time.sleep(2)
    raise RuntimeError("cluster not formed")


def emqx_cpu():
    out = sh("docker", "stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}}", check=False)
    return {l.split()[0][6:]: float(l.split()[1].rstrip("%")) for l in out.splitlines()
            if l.startswith("lab10-emqx") and len(l.split()) == 2}


def prepare(max_wait=120):
    """Перед прогоном: новый контейнер генератора (никаких процессов и сокетов от прошлого прогона)
    и пустые простаивающие брокеры: подключений не больше служебных, CPU < 30 % два замера подряд."""
    sh("docker", "restart", "-t", "1", "lab10-loadgen")
    t, calm, conns, cpu = time.time(), 0, None, {}
    while time.time() - t < max_wait:
        conns = sum((v.get("connections") or 0) for v in emqx_nodes().values())
        cpu = emqx_cpu()
        calm = calm + 1 if conns <= 10 and all(v < 30 for v in cpu.values()) else 0
        if calm >= 2:
            break
        time.sleep(2)
    state = dict(wait_s=round(time.time() - t, 1), conns=conns, cpu=cpu)
    print(f"   idle check: {state}", flush=True)
    return state


def emqx_metrics():
    """Сумма метрик по узлам, доступным через API emqx1."""
    out = {}
    try:
        for node in api("metrics?aggregate=false"):
            for k, v in node.items():
                if isinstance(v, int):
                    out[k] = out.get(k, 0) + v
    except Exception:
        pass
    return out


def beam_fds(container):
    r = sh("docker", "exec", container, "sh", "-c", "ls /proc/$(pgrep -o -x beam.smp)/fd | wc -l", check=False)
    return int(r.strip()) if r.strip().isdigit() else None


def kafka_lag():
    r = sh("docker", "exec", "lab10-redpanda", "rpk", "group", "describe", "ingest", "-X", "brokers=localhost:9092",
           check=False)
    for line in r.splitlines():
        if line.startswith("TOTAL-LAG"):
            try:
                return int(line.split()[1])
            except ValueError:
                return None
    return None


# ---------- фоновый сбор ресурсов ----------

class Sampler(threading.Thread):
    """docker stats (~2 с на снимок) + число подключений по узлам EMQX + лаг Kafka."""

    def __init__(self, kafka=False):
        super().__init__(daemon=True)
        self.kafka = kafka
        self.rows = []
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            t = time.time()
            out = sh("docker", "stats", "--no-stream", "--format", "{{json .}}", check=False)
            row = {"t": t, "cpu": {}, "mem": {}}
            for line in out.splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not d["Name"].startswith("lab10-"):
                    continue
                n = d["Name"][6:]
                row["cpu"][n] = float(d["CPUPerc"].rstrip("%") or 0)
                mem = d["MemUsage"].split("/")[0].strip()
                mult = {"GiB": 1024, "MiB": 1, "KiB": 1 / 1024, "B": 1 / 1048576}
                for u, k in mult.items():
                    if mem.endswith(u):
                        row["mem"][n] = float(mem[: -len(u)]) * k
                        break
            row["conns"] = {k: v.get("connections") for k, v in emqx_nodes().items()}
            if self.kafka:
                row["lag"] = kafka_lag()
            self.rows.append(row)


# ---------- конфигурации стенда ----------

def stage_single():
    compose("stop", "nginx", "emqx2", "bridge", "ingest", "redpanda")
    compose("up", "-d", "emqx1", "timescale", "loadgen", "prometheus", "grafana")
    wait_healthy("lab10-emqx1")
    wait_healthy("lab10-timescale")
    compose("up", "-d", "--force-recreate", "--scale", "ingest-mqtt=1", "ingest-mqtt",
            env_extra={"INGEST_MQTT_HOST": "emqx1"})
    time.sleep(3)


def stage_cluster():
    compose("stop", "bridge", "ingest", "redpanda")
    compose("up", "-d", "emqx1", "emqx2", "nginx", "timescale", "loadgen", "prometheus", "grafana")
    wait_healthy("lab10-emqx1")
    wait_healthy("lab10-emqx2")
    wait_cluster(2)
    compose("restart", "nginx")
    compose("up", "-d", "--force-recreate", "--scale", "ingest-mqtt=1", "ingest-mqtt",
            env_extra={"INGEST_MQTT_HOST": "emqx1"})
    time.sleep(3)


def stage_kafka(n_ingest, n_bridge=4):
    compose("stop", "ingest-mqtt")
    compose("up", "-d", "emqx1", "emqx2", "nginx", "timescale", "loadgen", "redpanda", "prometheus", "grafana")
    wait_healthy("lab10-emqx1")
    wait_healthy("lab10-emqx2")
    wait_cluster(2)
    wait_healthy("lab10-redpanda")
    compose("up", "redpanda-init")
    compose("up", "-d", "--scale", f"bridge={n_bridge}", "--scale", f"ingest={n_ingest}", "--no-recreate",
            "bridge", "ingest")
    time.sleep(8)          # перераспределение партиций в группе


# ---------- один прогон ----------

def run(name, clients, host="nginx", rate=0.2, duration=60, policy="exp_jitter", buffer=False, kafka=False,
        event=None, extra=None, conn_rate=500, backoff_base=1.0):
    """event = (секунд после начала публикации, функция) — отказ под нагрузкой."""
    print(f"\n=== {name}: {clients} клиентов, {rate}/с, {duration} с ===", flush=True)
    idle = prepare()
    psql("TRUNCATE telemetry")
    m0 = emqx_metrics()
    smp = Sampler(kafka=kafka)
    smp.start()
    t0 = time.time() + 3
    start_pub = t0 + clients / conn_rate
    cmd = ["docker", "exec", "lab10-loadgen", "python", "/app/loadgen.py", "--run", name, "--clients", str(clients),
           "--rate", str(rate), "--duration", str(duration), "--host", host, "--policy", policy, "--t0", str(t0), "--conn-rate", str(conn_rate),
           "--backoff-base", str(backoff_base)]
    if buffer:
        cmd.append("--buffer")
    ev_log = {}
    if event:
        def fire():
            time.sleep(max(0, start_pub + event[0] - time.time()))
            ev_log["t"] = time.time()
            ev_log["what"] = event[1].__doc__
            event[1]()
            ev_log["done"] = time.time()
        threading.Thread(target=fire, daemon=True).start()
    fds = {}

    def probe_fds():
        time.sleep(max(0, start_pub + 15 - time.time()))
        for c in ("lab10-emqx1", "lab10-emqx2", "lab10-emqx3"):
            fds[c] = beam_fds(c)
    threading.Thread(target=probe_fds, daemon=True).start()
    lg_out = sh(*cmd)
    print(lg_out, flush=True)
    t_end_pub = time.time()
    # дождаться, пока конвейер разберёт хвост (лаг/очереди), но не дольше 120 с
    last, stable = -1, 0
    while time.time() - t_end_pub < 120:
        n = int(psql(f"SELECT count(*) FROM telemetry WHERE run='{name}'").strip())
        stable = stable + 1 if n == last else 0
        if stable >= 3 and (not kafka or (kafka_lag() or 0) == 0):
            break
        last = n
        time.sleep(2)
    time.sleep(2)
    smp.stop.set()
    smp.join()
    m1 = emqx_metrics()
    lg = json.load(open(os.path.join(RUNS, f"{name}_loadgen.json")))
    res = analyze(name, lg)
    res.update(
        name=name, clients=clients, rate=rate, duration=duration, policy=policy, buffer=buffer, kafka=kafka,
        host=host, event=ev_log, fds=fds, extra=extra or {}, idle_before=idle,
        emqx_delta={k: m1.get(k, 0) - m0.get(k, 0) for k in m1 if m1.get(k, 0) - m0.get(k, 0)},
        samples=smp.rows, loadgen=lg,
    )
    summ = summary_text(res)
    json.dump(res, open(os.path.join(RUNS, f"{name}.json"), "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(CON, f"{name}.txt"), "w", encoding="utf-8").write(lg_out + summ)
    print(summ, flush=True)
    time.sleep(10)                       # пауза между прогонами
    return res


def analyze(name, lg):
    q = psql(f"""
      SELECT count(*), count(DISTINCT (device_id, seq)),
             percentile_cont(ARRAY[0.5, 0.95, 0.99]) WITHIN GROUP (ORDER BY extract(epoch FROM stored_at)*1000 - ts_ms),
             max(extract(epoch FROM stored_at)*1000 - ts_ms)
      FROM telemetry WHERE run='{name}'""").strip().split("|")
    rows, distinct = int(q[0]), int(q[1])
    p = [float(x) for x in q[2].strip("{}").split(",")] if rows else [None] * 3
    # пропускная способность записи в БД (по моменту записи) — посекундно
    tp = psql(f"""SELECT floor(extract(epoch FROM stored_at))::bigint, count(*) FROM telemetry
                  WHERE run='{name}' GROUP BY 1 ORDER BY 1""")
    tput = [(int(a), int(b)) for a, b in (l.split("|") for l in tp.splitlines() if l)]
    # задержка по моменту измерения — посекундно p50/p99
    lt = psql(f"""SELECT ts_ms/1000, count(*),
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM stored_at)*1000 - ts_ms),
                  percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM stored_at)*1000 - ts_ms)
                  FROM telemetry WHERE run='{name}' GROUP BY 1 ORDER BY 1""")
    lat = [(int(a), int(b), float(c), float(d)) for a, b, c, d in (l.split("|") for l in lt.splitlines() if l)]
    s0, s1 = lg["start_pub"] + 10, lg["end_t"]          # установившийся режим
    steady = [n for t, n in tput if s0 <= t < s1]
    c = lg["counters"]
    # установившийся режим (без разгона подключений): p50/p99 сквозной задержки и по участкам
    # устройство → мост (метка Kafka), мост → consumer, consumer → БД
    w = f"run='{name}' AND ts_ms >= {int(s0 * 1000)} AND ts_ms < {int(s1 * 1000)}"
    pc = "percentile_cont(ARRAY[0.5, 0.99]) WITHIN GROUP (ORDER BY {})"
    e2e = "extract(epoch FROM stored_at)*1000 - ts_ms"
    qs = psql(f"""SELECT count(*), {pc.format(e2e)}, {pc.format('kafka_ms - ts_ms')},
                  {pc.format('recv_ms - kafka_ms')}, {pc.format('extract(epoch FROM stored_at)*1000 - recv_ms')},
                  avg((({e2e}) <= 1000)::int) FROM telemetry WHERE {w}""").strip().split("|")

    def arr(x):
        x = x.strip("{}")
        return [float(v) for v in x.split(",")] if x and x != "NULL" else [None, None]
    steady_lat = dict(n=int(qs[0]), e2e=arr(qs[1]), to_bridge=arr(qs[2]), kafka=arr(qs[3]), to_db=arr(qs[4]),
                      within_1s=float(qs[5]) if qs[5] else None)
    return dict(
        steady_lat=steady_lat,
        stored=rows, stored_distinct=distinct, duplicates=rows - distinct,
        lat_p50=p[0], lat_p95=p[1], lat_p99=p[2], lat_max=float(q[3]) if rows else None,
        tput_steady=sum(steady) / len(steady) if steady else 0, tput_max=max((n for _, n in tput), default=0),
        offered=lg["args"]["clients"] * lg["args"]["rate"],
        lost_after_ack=max(0, c["acked"] - distinct), not_stored=c["generated"] - distinct,
        tput_series=tput, lat_series=lat,
    )


def summary_text(r):
    c = r["loadgen"]["counters"]
    cpu = {}
    for row in r["samples"]:
        for k, v in row["cpu"].items():
            cpu.setdefault(k, []).append(v)
    cpu_s = ", ".join(f"{k} {max(v):.0f}%" for k, v in sorted(cpu.items()) if max(v) > 5)
    lg = r["loadgen"]
    st = {}
    for row in r["samples"]:
        if lg["start_pub"] + 10 <= row["t"] <= lg["end_t"]:
            for k, v in row["cpu"].items():
                st.setdefault(k, []).append(v)
    r["cpu_steady"] = {k: sum(v) / len(v) for k, v in st.items()}
    r["cpu_peak"] = {k: max(v) for k, v in cpu.items()}
    st_s = ", ".join(f"{k} {v:.0f}%" for k, v in sorted(r["cpu_steady"].items()) if v > 5)
    d = r["emqx_delta"]
    lines = [
        f"-- {r['name']}: stored {r['stored_distinct']} of acked {c['acked']} (generated {c['generated']}), "
        f"duplicates {r['duplicates']}",
        f"   e2e latency p50/p95/p99/max = {fmt(r['lat_p50'])}/{fmt(r['lat_p95'])}/{fmt(r['lat_p99'])}/"
        f"{fmt(r['lat_max'])} ms",
        f"   steady window: p50/p99 e2e {fmtl(r['steady_lat']['e2e'])} ms; device→bridge "
        f"{fmtl(r['steady_lat']['to_bridge'])}, kafka {fmtl(r['steady_lat']['kafka'])}, "
        f"consumer→db {fmtl(r['steady_lat']['to_db'])}; ≤1 s: {fmtp(r['steady_lat']['within_1s'])}",
        f"   throughput steady {r['tput_steady']:.0f} msg/s (offered {r['offered']:.0f}), max {r['tput_max']}",
        f"   emqx: received {d.get('messages.received', 0)} delivered {d.get('messages.delivered', 0)} "
        f"queue_full {d.get('delivery.dropped.queue_full', 0)} no_subscribers {d.get('messages.dropped.no_subscribers', 0)}",
        f"   peak CPU: {cpu_s}",
        f"   steady CPU: {st_s}",
        f"   fds: {r['fds']}",
    ]
    if r["kafka"]:
        lags = [row.get("lag") for row in r["samples"] if row.get("lag") is not None]
        lines.append(f"   kafka lag max {max(lags) if lags else None}")
    if r["event"]:
        lines.append(f"   event: {r['event'].get('what')} at +{r['event']['t'] - r['loadgen']['start_pub']:.1f}s")
    return "\n".join(lines) + "\n"


def fmt(x):
    return "-" if x is None else f"{x:.0f}"


def fmtl(pair):
    return "/".join(fmt(x) for x in pair)


def fmtp(x):
    return "-" if x is None else f"{x * 100:.2f} %"


# ---------- этапы ----------

def kill_emqx2():
    """kill -9 узла emqx2"""
    sh("docker", "kill", "lab10-emqx2")


def restart_nginx():
    """перезапуск NGINX: все соединения рвутся разом"""
    sh("docker", "restart", "-t", "0", "lab10-nginx")


def fresh_brokers():
    """Чистые узлы EMQX перед этапом: последствия перегрузки прошлого прогона не переносятся."""
    sh("docker", "compose", "up", "-d", "--force-recreate", "emqx1", "emqx2")
    wait_healthy("lab10-emqx1")
    wait_healthy("lab10-emqx2")
    wait_cluster(2)
    sh("docker", "compose", "restart", "nginx")
    time.sleep(5)


TUNED = {"EMQX_MQUEUE": "10000", "EMQX_INFLIGHT": "128", "EMQX_MAILBOX": "10000"}


def main(stages):
    for st in stages:
        if st == "tuned":                 # с этого этапа и далее — лимиты сессий для серверных подписчиков
            ENV.update(TUNED)
        if st in ("kafka", "tuned", "drain", "failover", "herd"):
            fresh_brokers()
        if st == "tuned":
            for n_ing, clients in ((2, 10000), (2, 20000)):
                stage_kafka(n_ing)
                run(f"tuned-{clients // 1000}k-c{n_ing}", clients, kafka=True, extra={"tuned": TUNED})
        if st == "three":                 # третий узел EMQX (VM с 1 vCPU), те же лимиты сессий
            ENV.update(TUNED)
            ENV.update(EMQX_SEEDS="[emqx@emqx1.lab10.io,emqx@emqx2.lab10.io,emqx@emqx3.lab10.io]",
                       NGINX_CONF="./nginx/nginx-3.conf", COMPOSE_PROFILES="three")
            sh("docker", "compose", "up", "-d", "--force-recreate", "emqx1", "emqx2", "emqx3")
            for c in ("lab10-emqx1", "lab10-emqx2", "lab10-emqx3"):
                wait_healthy(c)
            wait_cluster(3)
            sh("docker", "compose", "up", "-d", "--force-recreate", "nginx")
            time.sleep(5)
            for clients in (10000, 20000):
                stage_kafka(2, n_bridge=6)
                run(f"three-{clients // 1000}k-c2", clients, kafka=True, extra={"tuned": TUNED, "nodes": 3})
        if st == "single":
            stage_single()
            for n in (1000, 5000, 10000):
                run(f"single-{n // 1000}k", n, host="emqx1")
        elif st == "cluster":
            stage_cluster()
            for n in (10000, 20000):
                run(f"cluster-{n // 1000}k", n)
        elif st == "kafka":
            for n_ing, clients in ((1, 10000), (2, 10000), (2, 20000)):
                stage_kafka(n_ing)
                run(f"kafka-{clients // 1000}k-c{n_ing}", clients, kafka=True)
        elif st == "drain":
            drain()
        elif st == "failover":
            stage_kafka(2)
            for buf in (False, True):
                run(f"failover-{'buffer' if buf else 'nobuffer'}", 5000, kafka=True, buffer=buf,
                    event=(20, kill_emqx2))
                compose("up", "-d", "emqx2")
                wait_healthy("lab10-emqx2")
                wait_cluster(2)
                compose("restart", "nginx")
                time.sleep(5)
        elif st == "herd":
            stage_kafka(2)
            for name, pol, base in (("immediate", "immediate", 1), ("exp", "exp", 1), ("exp_jitter", "exp_jitter", 1),
                                    ("exp_jitter10", "exp_jitter", 10)):
                run(f"herd-{name}", 10000, rate=0.1, duration=100, policy=pol, kafka=True, backoff_base=base,
                    event=(15, restart_nginx), extra={"backoff_base": base})
                time.sleep(5)


def drain(messages=3_000_000):
    """Пропускная способность consumer group: одинаковая очередь (3 млн сообщений, 8 партиций), N = 1, 2, 4.
    Очередь наполняется прямо в Kafka (app/fill_kafka.py) — опыт меряет только разбор группой.
    Темп = 80 % строк / время между моментами записи 10-го и 90-го процентиля (без старта и хвоста)."""
    stage_kafka(1)
    compose("up", "-d", "--scale", "ingest=0", "ingest")
    compose("stop", "bridge")                 # в топик пишет только наполнитель
    rpk = ("docker", "exec", "lab10-redpanda", "rpk")
    sh(*rpk, "group", "delete", "ingest", "-X", "brokers=localhost:9092", check=False)
    sh(*rpk, "topic", "delete", "telemetry", "-X", "brokers=localhost:9092", check=False)
    sh(*rpk, "topic", "create", "telemetry", "-p", "8", "-r", "1", "-X", "brokers=localhost:9092")
    print("\n=== drain: наполнение Kafka ===", flush=True)
    fill = sh("docker", "exec", "lab10-loadgen", "python", "/app/fill_kafka.py", "--messages", str(messages))
    print(fill.strip(), flush=True)
    backlog = kafka_lag_from_offsets()
    print(f"backlog in topic: {backlog}", flush=True)
    out = []
    for n in (1, 2, 4):
        compose("up", "-d", "--scale", "ingest=0", "ingest")
        time.sleep(3)
        sh(*rpk, "group", "delete", "ingest", "-X", "brokers=localhost:9092", check=False)   # новая группа читает с начала
        psql("TRUNCATE telemetry")
        smp = Sampler(kafka=False)
        smp.start()
        t = time.time()
        compose("up", "-d", "--scale", f"ingest={n}", "ingest")
        while True:
            time.sleep(2)
            cnt = int(psql("SELECT count(*) FROM telemetry").strip())
            if cnt >= backlog or time.time() - t > 900:
                break
        el = time.time() - t
        smp.stop.set()
        smp.join()
        q = psql("""SELECT extract(epoch FROM min(stored_at)), extract(epoch FROM max(stored_at)),
                    extract(epoch FROM percentile_disc(0.1) WITHIN GROUP (ORDER BY stored_at)),
                    extract(epoch FROM percentile_disc(0.9) WITHIN GROUP (ORDER BY stored_at)),
                    count(DISTINCT (device_id, seq)) FROM telemetry""").strip().split("|")
        first, last, t10, t90, distinct = float(q[0]), float(q[1]), float(q[2]), float(q[3]), int(q[4])
        rate = 0.8 * cnt / (t90 - t10)
        tp = psql("SELECT floor(extract(epoch FROM stored_at))::bigint, count(*) FROM telemetry GROUP BY 1 ORDER BY 1")
        series = [(int(x), int(y)) for x, y in (l.split("|") for l in tp.splitlines() if l)]
        cpu = {}
        for row in smp.rows:
            if t10 <= row["t"] <= t90:
                for k, v in row["cpu"].items():
                    cpu.setdefault(k, []).append(v)
        cpu = {k: sum(v) / len(v) for k, v in cpu.items()}
        r = dict(n=n, backlog=backlog, stored=cnt, distinct=distinct, elapsed=el, startup=first - t,
                 rate=rate, series=series, cpu=cpu, samples=smp.rows)
        out.append(r)
        print(f"drain N={n}: {cnt} rows ({distinct} distinct), first row after {first - t:.1f}s, "
              f"done in {last - first:.1f}s; steady {rate:,.0f} rows/s; "
              f"CPU: " + ", ".join(f"{k} {v:.0f}%" for k, v in sorted(cpu.items()) if v > 10), flush=True)
    json.dump(out, open(os.path.join(RUNS, "drain.json"), "w"))
    base = out[0]["rate"]
    text = "\n".join(f"N={r['n']}: steady {r['rate']:,.0f} msg/s, speedup {r['rate'] / base:.2f}x "
                     f"(ideal {r['n']}x, efficiency {r['rate'] / base / r['n'] * 100:.0f}%); "
                     + ", ".join(f"{k} {v:.0f}%" for k, v in sorted(r["cpu"].items()) if v > 10) for r in out)
    open(os.path.join(CON, "drain.txt"), "w", encoding="utf-8").write(
        f"backlog {backlog:,} messages in 8 partitions\n{text}\n")
    print(text)
    compose("up", "-d", "--scale", "ingest=1", "ingest")
    compose("start", "bridge")


def kafka_lag_from_offsets():
    r = sh("docker", "exec", "lab10-redpanda", "rpk", "topic", "describe", "telemetry", "-p", "-X",
           "brokers=localhost:9092")
    total = 0
    for line in r.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6 and parts[0].isdigit():
            total += int(parts[-1]) - int(parts[-2])     # HIGH-WATERMARK − LOG-START-OFFSET
    return total


if __name__ == "__main__":
    main(sys.argv[1:] or ["single", "cluster", "kafka", "drain", "failover", "herd"])
