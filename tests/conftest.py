from __future__ import annotations

import random
from collections.abc import Callable, Iterable

import httpx
import pytest

from resilient_http import FakeClock

Step = int | Exception | httpx.Response


class ScriptedServer:
    """Servidor falso: responde seguindo um roteiro, um passo por requisição.

    Cada passo é um status (int), uma ``httpx.Response`` pronta ou uma exceção
    a ser lançada (simula falha de rede). Após o fim do roteiro repete o último.
    """

    def __init__(self, script: Iterable[Step]) -> None:
        self.script = list(script)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        if isinstance(step, httpx.Response):
            return step
        return httpx.Response(step, json={"n": len(self.requests)})

    @property
    def calls(self) -> int:
        return len(self.requests)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def rng() -> random.Random:
    return random.Random(1234)


@pytest.fixture
def server_factory() -> Callable[[Iterable[Step]], ScriptedServer]:
    return ScriptedServer
