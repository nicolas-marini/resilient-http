"""Abstração de tempo.

Toda a lógica lê o tempo e dorme por meio de um ``Clock``. Em produção,
``SystemClock``; em testes e no simulador, ``FakeClock``, em que dormir apenas
avança um contador — testes de backoff rodam instantaneamente e são
determinísticos.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
    async def asleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    async def asleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)


class FakeClock:
    """Relógio controlado manualmente. ``sleeps`` registra cada espera pedida."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def set(self, now: float) -> None:
        self._now = now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += max(0.0, seconds)

    async def asleep(self, seconds: float) -> None:
        self.sleep(seconds)
