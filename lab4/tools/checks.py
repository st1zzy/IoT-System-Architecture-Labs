"""Восемь проверок производственной конфигурации брокера (запускается в lab4-client).

    docker exec lab4-client python tools/checks.py            # все проверки
    docker exec lab4-client python tools/checks.py 2 5        # выборочно
Шаг 3 перезапускает брокер A — нужен доступ к Docker с хоста, поэтому рестарт
выполняет run_checks.sh, а скрипт ждёт по файлу-флагу.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

V2 = mqtt.CallbackAPIVersion.VERSION2
A, B, EMQX = "lab4-mosquitto-a", "lab4-mosquitto-b", "lab4-emqx"
OUT = Path("/lab/results")
CRED = dict(line.strip().split("=", 1) for line in open("/secrets/creds.env") if "=" in line)
RESULTS: list[dict] = []


def pw(user: str) -> str:
    return CRED["PASS_" + user.upper().replace("-", "_")]


def client(cid, user=None, password=None, host=A, v5=False, clean=True, will=None):
    c = mqtt.Client(V2, client_id=cid, protocol=mqtt.MQTTv5 if v5 else mqtt.MQTTv311,
                    **({} if v5 else {"clean_session": clean}))
    if user:
        c.username_pw_set(user, password if password is not None else pw(user))
    return c


def connect(c, host=A, v5=False, clean=True, timeout=5):
    """Возвращает код CONNACK (0 — успех)."""
    res = {}
    ev = threading.Event()

    def on_connect(_c, _u, _f, rc, _p):
        res["rc"] = rc
        ev.set()

    c.on_connect = on_connect
    if v5:
        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = 0 if clean else 3600
        c.connect(host, 1883, clean_start=clean, properties=props)
    else:
        c.connect(host, 1883)
    c.loop_start()
    ev.wait(timeout)
    return res.get("rc")


def collect(c, flt, qos=1, seconds=2.0):
    got = []
    c.on_message = lambda _c, _u, m: got.append((m.topic, m.payload))
    c.subscribe(flt, qos=qos)
    time.sleep(seconds)
    return got


def record(n, name, ok, details):
    RESULTS.append({"n": n, "check": name, "ok": ok, "details": details})
    print(f"[{'ПРОЙДЕНО' if ok else 'НЕ ПРОЙДЕНО'}] {n}. {name}")
    for d in details:
        print(f"      {d}")


def check1():
    det, ok = [], True
    for user, password, label in ((None, None, "анонимно"), ("device01", "wrong", "device01 с неверным паролем")):
        rc = connect(client(f"auth-{label[:6]}", user, password))
        det.append(f"{label}: CONNACK {rc}")
        ok &= rc is not None and rc != 0
    for u in ("device01", "gateway01", "svc-ingest", "dashboard"):
        c = client(f"auth-{u}", u)
        rc = connect(c)
        det.append(f"{u}: CONNACK {rc}")
        ok &= rc == 0
        c.disconnect()
    record(1, "Аутентификация по password file", ok, det)


def check2():
    det, ok = [], True
    ing = client("acl-ingest", "svc-ingest")
    connect(ing)
    got = []
    ing.on_message = lambda _c, _u, m: got.append(m.topic)
    ing.subscribe("enu/+/+/up/#", qos=1)
    time.sleep(0.5)
    own, foreign = "enu/site1/device01/up/telemetry/temperature", "enu/site1/device02/up/telemetry/temperature"
    d3 = client("acl-dev01", "device01")
    connect(d3)
    d3.publish(own, '{"seq":1,"value":21.5}', qos=1).wait_for_publish(3)
    info = d3.publish(foreign, '{"seq":1,"value":99.9}', qos=1)
    info.wait_for_publish(3)
    det.append(f"MQTT 3.1.1, device01 → свой топик: PUBACK получен, ingest получил: {own in got}")
    det.append(f"MQTT 3.1.1, device01 → топик device02: PUBACK получен ({info.is_published()}), "
               f"ingest получил: {foreign in got} — запрет молчаливый")
    ok &= own in got and foreign not in got
    d3.disconnect()
    # MQTT 5: тот же запрет виден клиенту как код причины в PUBACK
    rcodes = {}
    d5 = client("acl-dev01-v5", "device01", v5=True)
    d5.on_publish = lambda _c, _u, mid, rc, _p: rcodes.__setitem__(mid, rc)
    connect(d5, v5=True)
    i5 = d5.publish(foreign, '{"seq":2,"value":99.9}', qos=1)
    time.sleep(1)
    rc5 = rcodes.get(i5.mid)
    det.append(f"MQTT 5, device01 → топик device02: PUBACK с кодом «{rc5}» ({getattr(rc5, 'value', '?')})")
    ok &= rc5 is not None and rc5.is_failure
    # подписка на чужие команды
    sub_rc = {}
    d5.on_subscribe = lambda _c, _u, mid, rcs, _p: sub_rc.__setitem__("rc", rcs)
    d5.subscribe("enu/site1/device02/down/#", qos=1)
    time.sleep(1)
    # mosquitto не отказывает в подписке, а проверяет право read при доставке каждого сообщения
    stolen = []
    d5.on_message = lambda _c, _u, m: stolen.append(m.topic)
    gw = client("acl-gw", "gateway01")
    connect(gw)
    gw.publish("enu/site1/device02/down/cmd", '{"cmd":"open"}', qos=1).wait_for_publish(3)
    time.sleep(1)
    gw.disconnect()
    det.append(f"MQTT 5, device01 SUBSCRIBE на down/ device02: SUBACK «{sub_rc.get('rc', ['?'])[0]}» — подписка "
               f"принята, но команду для device02 device01 получил: {bool(stolen)} (запрет при доставке)")
    ok &= bool(sub_rc) and not stolen
    d5.disconnect()
    # дашборд не может отправить команду
    dash = client("acl-dash-v5", "dashboard", v5=True)
    connect(dash, v5=True)
    rcodes.clear()
    dash.on_publish = lambda _c, _u, mid, rc, _p: rcodes.__setitem__(mid, rc)
    i6 = dash.publish("enu/site1/device01/down/cmd", '{"cmd":"reboot"}', qos=1)
    time.sleep(1)
    det.append(f"MQTT 5, dashboard → команда устройству: PUBACK «{rcodes.get(i6.mid)}»")
    ok &= rcodes.get(i6.mid) is not None and rcodes[i6.mid].is_failure
    dash.disconnect()
    ing.disconnect()
    record(2, "ACL: запрет публикации и подписки на чужие топики", ok, det)


def wait_flag(name, timeout=60):
    f = OUT / name
    t = time.time()
    while not f.exists() and time.time() - t < timeout:
        time.sleep(0.3)
    return f.exists()


def check3():
    det = []
    sub = client("persist-ingest", "svc-ingest", clean=False)
    connect(sub, clean=False)
    sub.subscribe("enu/site1/device01/up/telemetry/humidity", qos=1)
    time.sleep(0.5)
    sub.disconnect(); sub.loop_stop()
    pub = client("persist-dev01", "device01")
    connect(pub)
    for i in range(20):
        pub.publish("enu/site1/device01/up/telemetry/humidity", json.dumps({"seq": i}), qos=1).wait_for_publish(3)
    pub.disconnect()
    det.append("svc-ingest (clean_session=false) подписан и отключён; device01 отправил 20 сообщений QoS 1")
    (OUT / "need_restart").write_text("1")
    if not wait_flag("restarted"):
        record(3, "Persistence: сессия и очередь после рестарта", False, det + ["рестарт не выполнен"])
        return
    (OUT / "restarted").unlink()
    time.sleep(2)
    sub = client("persist-ingest", "svc-ingest", clean=False)
    got = []
    sub.on_message = lambda _c, _u, m: got.append(json.loads(m.payload)["seq"])
    flags = {}
    sub.on_connect = lambda _c, _u, f, rc, _p: flags.update(sp=f.session_present, rc=rc)
    sub.connect(A, 1883)
    sub.loop_start()
    time.sleep(3)
    sub.disconnect()
    det.append(f"брокер A перезапущен; повторное подключение: session_present={flags.get('sp')}, "
               f"получено {len(got)} из 20 (seq {min(got) if got else '-'}…{max(got) if got else '-'})")
    record(3, "Persistence: сессия и очередь после рестарта", len(got) == 20 and flags.get("sp"), det)


def check4():
    det, ok = [], True
    ing = client("lim-ingest", "svc-ingest")
    connect(ing)
    got = []
    ing.on_message = lambda _c, _u, m: got.append(len(m.payload))
    ing.subscribe("enu/site1/device01/up/telemetry/pressure", qos=1)
    time.sleep(0.5)
    d = client("lim-dev01", "device01")
    connect(d)
    for size in (900, 2048):
        d.publish("enu/site1/device01/up/telemetry/pressure", b"x" * size, qos=1).wait_for_publish(3)
    time.sleep(1)
    det.append(f"message_size_limit 1024: отправлено 900 и 2048 Б, получено {got}")
    ok &= got == [900]
    ing.disconnect()
    # очередь offline-клиента: max_queued_messages 100
    q = client("lim-queue", "svc-ingest", clean=False)
    connect(q, clean=False)
    q.subscribe("enu/site1/device01/up/telemetry/queue", qos=1)
    time.sleep(0.5)
    q.disconnect(); q.loop_stop()
    for i in range(150):
        d.publish("enu/site1/device01/up/telemetry/queue", json.dumps({"seq": i}), qos=1).wait_for_publish(3)
    d.disconnect()
    q = client("lim-queue", "svc-ingest", clean=False)
    got2 = []
    q.on_message = lambda _c, _u, m: got2.append(json.loads(m.payload)["seq"])
    q.connect(A, 1883)
    q.loop_start()
    time.sleep(3)
    q.disconnect()
    det.append(f"max_queued_messages 100: офлайн-подписчику отправлено 150, после подключения получено "
               f"{len(got2)} (seq {min(got2)}…{max(got2)}) — остальные отброшены брокером")
    ok &= len(got2) == 100
    record(4, "Лимиты: message_size_limit и max_queued_messages", ok, det)


def check5():
    det, ok = [], True
    sa, sb = client("br-sub-a", "svc-ingest"), client("br-sub-b")
    connect(sa); connect(sb, host=B)
    ga, gb = [], []
    sa.on_message = lambda _c, _u, m: ga.append(m.topic)
    sb.on_message = lambda _c, _u, m: gb.append(m.topic)
    sa.subscribe("enu/+/+/status", 1); sb.subscribe("enu/#", 1)
    time.sleep(1)
    d = client("br-dev01", "device01")
    connect(d)
    d.publish("enu/site1/device01/up/telemetry/temperature", '{"seq":7}', qos=1).wait_for_publish(3)
    d.publish("enu/site1/device01/status", "online", qos=1).wait_for_publish(3)
    time.sleep(2)
    n_tel = gb.count("enu/site1/device01/up/telemetry/temperature")
    det.append(f"A → B: телеметрия получена в B {n_tel} раз (out), статус: в A {ga.count('enu/site1/device01/status')}, "
               f"в B {gb.count('enu/site1/device01/status')} раз (both)")
    ok &= n_tel == 1 and ga.count("enu/site1/device01/status") == 1 and gb.count("enu/site1/device01/status") == 1
    pb = client("br-pub-b")
    connect(pb, host=B)
    pb.publish("enu/site1/device02/status", "offline", qos=1).wait_for_publish(3)
    pb.publish("enu/site1/device02/down/cmd", '{"cmd":"ping"}', qos=1).wait_for_publish(3)
    time.sleep(2)
    det.append(f"B → A: статус device02 в A {ga.count('enu/site1/device02/status')} раз, в B "
               f"{gb.count('enu/site1/device02/status')} раз — без петли (каждая копия ровно одна)")
    ok &= ga.count("enu/site1/device02/status") == 1 and gb.count("enu/site1/device02/status") == 1
    sc = client("br-cmd", "device02")
    connect(sc)
    cmds = collect(sc, "enu/site1/device02/down/#", seconds=0.5)
    pb.publish("enu/site1/device02/down/cmd", '{"cmd":"ping2"}', qos=1).wait_for_publish(3)
    time.sleep(1.5)
    det.append(f"B → A (in): команда из B дошла до device02 в A: {len(cmds) >= 1}")
    ok &= len(cmds) >= 1
    for c in (sa, sb, d, pb, sc):
        c.disconnect()
    record(5, "Bridge A ⇄ B: передача и отсутствие петель", ok, det)


def check6():
    c = client("sys-dash", "dashboard")
    connect(c)
    vals = {}
    c.on_message = lambda _c, _u, m: vals.__setitem__(m.topic, m.payload.decode())
    c.subscribe("$SYS/#", qos=0)
    time.sleep(7)
    c.disconnect()
    keys = ["$SYS/broker/version", "$SYS/broker/uptime", "$SYS/broker/clients/connected",
            "$SYS/broker/clients/total", "$SYS/broker/subscriptions/count", "$SYS/broker/retained messages/count",
            "$SYS/broker/store/messages/count", "$SYS/broker/messages/received", "$SYS/broker/messages/sent",
            "$SYS/broker/publish/messages/dropped", "$SYS/broker/load/messages/received/1min",
            "$SYS/broker/load/bytes/received/1min", "$SYS/broker/heap/current",
            "$SYS/broker/connection/bridge-a/state"]
    det = [f"{k} = {vals.get(k, '—')}" for k in keys]
    det.append(f"всего топиков $SYS: {len(vals)}")
    (OUT / "sys.json").write_text(json.dumps(vals, indent=2, ensure_ascii=False), encoding="utf-8")
    record(6, "$SYS: метрики брокера", len(vals) > 30, det)


def shared(host, user, n=3000):
    flt = "$share/g1/enu/+/+/up/telemetry/#"
    counts = [0, 0, 0]
    cons = []
    for i in range(3):
        c = client(f"shared-{host[-6:]}-{i}", user, v5=True)
        c.on_message = (lambda k: (lambda _c, _u, m: counts.__setitem__(k, counts[k] + 1)))(i)
        connect(c, host=host, v5=True)
        c.subscribe(flt, qos=1)
        cons.append(c)
    time.sleep(1)
    p = client(f"shared-pub-{host[-6:]}", "device01" if user else None)
    connect(p, host=host)
    for s in range(n):
        p.publish("enu/site1/device01/up/telemetry/temperature", json.dumps({"seq": s}), qos=1)
    time.sleep(4)
    for c in cons + [p]:
        c.disconnect()
    return counts


def check7():
    c = client("emqx-probe")
    rc = connect(c, host=EMQX)
    c.disconnect()
    record(7, "EMQX: развёртывание и подключение", rc == 0,
           [f"MQTT-клиент к EMQX 5.8.0: CONNACK {rc}; состояние узла и статистика — в выводе emqx ctl (run_checks.sh)"])


def check8():
    det, ok = [], True
    for name, host, user in (("mosquitto A", A, "svc-ingest"), ("EMQX", EMQX, None)):
        counts = shared(host, user)
        share = [round(100 * x / sum(counts), 1) if sum(counts) else 0 for x in counts]
        det.append(f"{name}: 3000 сообщений → consumer'ы {counts} = {share} %, сумма {sum(counts)}")
        ok &= sum(counts) == 3000 and min(counts) > 0.25 * 3000
    record(8, "MQTT 5 shared subscription: распределение нагрузки", ok, det)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    which = [int(x) for x in sys.argv[1:]] or list(range(1, 9))
    for n in which:
        globals()[f"check{n}"]()
    (OUT / "checks.json").write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Итог: пройдено {sum(r['ok'] for r in RESULTS)} из {len(RESULTS)}")


if __name__ == "__main__":
    main()
