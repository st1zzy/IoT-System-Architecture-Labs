"""Эксперимент retained: подписка после публикации.

1) retained config и status → новый подписчик получает их сразу, с флагом retain;
2) телеметрия без retain → опоздавший подписчик её не получает;
3) пустой retained-payload удаляет сохранённое сообщение;
4) wildcard по статусам — «снимок» presence всего парка одной подпиской.
    python tools/retained_test.py
"""

import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).parent))
from topics import config_topic, status_topic, topic  # noqa: E402

V2 = mqtt.CallbackAPIVersion.VERSION2
H = "lab3-mosquitto"


def late_subscriber(flt: str, wait: float = 2.0) -> list[tuple[str, str, bool, float]]:
    got = []
    t_sub = [0.0]
    c = mqtt.Client(V2, client_id=f"late-{time.time_ns()}")
    c.on_message = lambda _c, _u, m: got.append((m.topic, m.payload.decode(), bool(m.retain),
                                                 (time.time() - t_sub[0]) * 1000))

    def on_connect(cl, *_):
        t_sub[0] = time.time()
        cl.subscribe(flt, qos=1)

    c.on_connect = on_connect
    c.connect(H, 1883)
    c.loop_start()
    time.sleep(wait)
    c.loop_stop()
    c.disconnect()
    return got


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    p = mqtt.Client(V2, client_id="retained-publisher")
    p.connect(H, 1883)
    p.loop_start()
    dev = "dev-001"
    p.publish(config_topic(dev), '{"interval_s":10,"schema_version":"1.0"}', qos=1, retain=True).wait_for_publish()
    p.publish(status_topic(dev), "online", qos=1, retain=True).wait_for_publish()
    p.publish(topic(dev, "temperature"), '{"seq":999,"value":21.5}', qos=1, retain=False).wait_for_publish()
    time.sleep(0.5)
    print("1) Публикация: config и status с retain=1, телеметрия temperature с retain=0; подписка через 0,5 с")
    for flt in (config_topic(dev), status_topic(dev), topic(dev, "temperature")):
        got = late_subscriber(flt)
        if got:
            for t, pl, rt, ms in got:
                print(f"   {flt}: получено «{pl}» retain={int(rt)} через {ms:.1f} мс после SUBSCRIBE")
        else:
            print(f"   {flt}: ничего — сообщение без retain опоздавшему подписчику недоступно")
    p.publish(config_topic(dev), b"", qos=1, retain=True).wait_for_publish()
    time.sleep(0.3)
    got = late_subscriber(config_topic(dev))
    print(f"2) Пустой payload с retain=1 удаляет сохранённое: новый подписчик на config получил {len(got)} сообщений")
    got = late_subscriber("enu/site1/+/status")
    print(f"3) Подписка enu/site1/+/status: {len(got)} retained-статусов сразу после подключения —")
    for t, pl, rt, ms in sorted(got):
        print(f"   {t} = {pl} (retain={int(rt)}, {ms:.1f} мс)")
    p.loop_stop()
    p.disconnect()


if __name__ == "__main__":
    main()
