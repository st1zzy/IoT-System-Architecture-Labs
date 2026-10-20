"""Эмулятор Modbus TCP: насосная станция, 4 насоса × 5 holding-регистров = 20 регистров.

Регистры насоса i (база 5·i): 0 температура подшипника ×10 °C (int16), 1 давление ×100 бар,
2 расход ×10 м³/ч, 3 ток двигателя ×10 А, 4 состояние (0 — стоп, 1 — работа).
Coil i = 1 запускает перегрев насоса i на 20 с (для проверки локального правила шлюза).
Насос 4 работает циклами: 90 с работа, 30 с стоп.
"""

import asyncio
import logging
import math
import random
import time

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.server import StartAsyncTcpServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("modbus-sim")
PUMPS = 4


def s16(x: float) -> int:
    return int(round(x)) & 0xFFFF


async def update(ctx: ModbusSlaveContext) -> None:
    t0 = time.time()
    temp = [44.0 + 3 * i for i in range(PUMPS)]
    overheat_until = [0.0] * PUMPS
    while True:
        t = time.time() - t0
        coils = ctx.getValues(1, 0, count=PUMPS)
        regs = []
        for i in range(PUMPS):
            if coils[i]:
                overheat_until[i] = time.time() + 20
                ctx.setValues(1, i, [0])
                log.info("перегрев насоса %d на 20 с", i + 1)
            running = 1 if i < 3 or (t % 120) < 90 else 0
            target = 95.0 if time.time() < overheat_until[i] else (44.0 + 3 * i if running else 30.0)
            temp[i] += (target - temp[i]) * (0.08 if time.time() < overheat_until[i] else 0.01)
            temp_meas = temp[i] + 0.8 * math.sin(2 * math.pi * t / 600) + random.gauss(0, 0.15)
            press = (3.5 + 0.1 * i + 0.05 * math.sin(2 * math.pi * t / 47) + random.gauss(0, 0.01)) if running else 0.4
            flow = (120 + 10 * i + 4 * math.sin(2 * math.pi * t / 63) + random.gauss(0, 0.8)) if running else 0.0
            curr = (15 + 1.5 * i + 0.3 * math.sin(2 * math.pi * t / 63) + random.gauss(0, 0.1)) if running else 0.0
            regs += [s16(temp_meas * 10), s16(press * 100), s16(flow * 10), s16(curr * 10), running]
        ctx.setValues(3, 0, regs)
        await asyncio.sleep(0.2)


async def main() -> None:
    slave = ModbusSlaveContext(hr=ModbusSequentialDataBlock(0, [0] * 20),
                               co=ModbusSequentialDataBlock(0, [0] * PUMPS), zero_mode=True)
    ctx = ModbusServerContext(slaves={1: slave}, single=False)
    asyncio.create_task(update(slave))
    log.info("Modbus TCP :5020, unit 1, holding 0..19, coils 0..3")
    await StartAsyncTcpServer(context=ctx, address=("0.0.0.0", 5020))


if __name__ == "__main__":
    asyncio.run(main())
