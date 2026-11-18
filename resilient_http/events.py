"""Eventos de observabilidade.

O cliente não faz log diretamente: emite ``Event`` para um callback opcional.
Quem usa decide o destino (logging, métricas, o dashboard de demonstração…).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

EventKind = Literal[
    "attempt",          # uma tentativa vai à rede
    "response",         # resposta recebida
    "error",            # exceção de transporte
    "retry_scheduled",  # vai esperar e tentar de novo
    "gave_up",          # política decidiu não tentar mais
    "rate_limited",     # esperou por um token
    "circuit_rejected", # chamada barrada pelo breaker
    "circuit_state",    # transição de estado do breaker
]


@dataclass(frozen=True)
class Event:
    kind: EventKind
    time: float
    data: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[Event], None]
