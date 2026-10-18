"""Пространство топиков (по правилам §10.7.1 и Practice 6).

{org}/{site}/{device_id}/{направление}/{класс}[/{метрика}] — от общего к частному;
направления up/down разделены, чтобы ACL выдавались по префиксу .../{device_id}/up/#.
"""

ORG, SITE = "enu", "site1"

METRICS = {                         # метрика: (единица, класс сообщения)
    "temperature": ("°C", "telemetry"),
    "humidity": ("%RH", "telemetry"),
    "pressure": ("hPa", "telemetry"),
    "motion": ("bool", "event"),
}

# Класс → (шаблон топика, QoS, retain, обоснование)
SPEC = [
    ("telemetry", "{org}/{site}/{dev}/up/telemetry/{metric}", 1, False,
     "основной поток; QoS 1 + идемпотентный приём по seq; retain на телеметрии — антипаттерн"),
    ("event", "{org}/{site}/{dev}/up/event/{metric}", 1, False,
     "редкие события (движение), потеря недопустима"),
    ("status", "{org}/{site}/{dev}/status", 1, True,
     "presence: online при подключении, offline — LWT; новый подписчик сразу видит состояние"),
    ("config", "{org}/{site}/{dev}/down/config", 1, True,
     "желаемая конфигурация: устройство получает её сразу после подключения"),
    ("command", "{org}/{site}/{dev}/down/cmd", 1, False,
     "команда не должна выполниться повторно при переподключении; ответ — .../up/cmd_res"),
]


def topic(dev: str, metric: str) -> str:
    cls = METRICS[metric][1]
    return f"{ORG}/{SITE}/{dev}/up/{cls}/{metric}"


def status_topic(dev: str) -> str:
    return f"{ORG}/{SITE}/{dev}/status"


def config_topic(dev: str) -> str:
    return f"{ORG}/{SITE}/{dev}/down/config"
