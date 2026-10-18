"""Что даёт `persistence true`: сравнение двух брокеров при перезапуске.

Сценарий для каждого брокера:
  1. consumer (clean_session=False) подписывается на demo/q с QoS 1 и отключается;
  2. publisher шлёт retained-сообщение и 5 сообщений QoS 1, пока consumer офлайн;
  3. брокер перезапускается (docker restart);
  4. consumer возвращается в ту же сессию — сколько сообщений он получит?
     и сохранилось ли retained-сообщение?

Пример:
    python tools/persistence_demo.py --case mosquitto:1883 --case mq-nopersist:1884
"""

import argparse
import json
import subprocess
import time

import paho.mqtt.client as mqtt

V2 = mqtt.CallbackAPIVersion.VERSION2


def wait_connect(c: mqtt.Client, port: int, tries: int = 30) -> None:
    for _ in range(tries):
        try:
            c.connect("localhost", port)
            return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"broker on :{port} is not reachable")


def collect(client_id: str, port: int, topic: str, clean: bool, seconds: float) -> list[str]:
    got: list[str] = []
    c = mqtt.Client(V2, client_id=client_id, clean_session=clean)
    c.on_message = lambda _c, _u, m: got.append(m.payload.decode())
    c.on_connect = lambda cl, *_: cl.subscribe(topic, qos=1)
    wait_connect(c, port)
    c.loop_start()
    time.sleep(seconds)
    c.loop_stop()
    c.disconnect()
    return got


def run_case(container: str, port: int) -> dict:
    # 1. регистрируем persistent-сессию и уходим офлайн
    collect("demo-consumer", port, "demo/q", clean=False, seconds=1)

    # 2. публикуем, пока consumer офлайн
    p = mqtt.Client(V2, client_id="demo-publisher")
    wait_connect(p, port)
    p.loop_start()
    p.publish("demo/retained", "state=42", qos=1, retain=True).wait_for_publish()
    for i in range(5):
        p.publish("demo/q", f"msg-{i}", qos=1).wait_for_publish()
    p.loop_stop()
    p.disconnect()

    # 3. перезапуск брокера (SIGTERM → корректное завершение)
    subprocess.run(["docker", "restart", container], check=True, capture_output=True)
    time.sleep(2)

    # 4. возвращаемся в сессию и проверяем retained
    queued = collect("demo-consumer", port, "demo/q", clean=False, seconds=3)
    retained = collect("demo-checker", port, "demo/retained", clean=True, seconds=2)

    # уборка: удаляем retained и сессию
    cl = mqtt.Client(V2, client_id="demo-consumer", clean_session=True)
    wait_connect(cl, port)
    cl.publish("demo/retained", b"", qos=1, retain=True)
    cl.loop(1)
    cl.disconnect()

    return {"broker": f"{container}:{port}", "queued_published": 5,
            "queued_received_after_restart": len(queued),
            "retained_after_restart": retained[0] if retained else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", required=True, help="container:port")
    ap.add_argument("--out")
    args = ap.parse_args()
    results = []
    for case in args.case:
        name, port = case.split(":")
        results.append(run_case(name, int(port)))
        print(json.dumps(results[-1], ensure_ascii=False))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
