"""Configuração imutável das políticas de resiliência.

Cada política é um dataclass congelado: fácil de compartilhar entre clientes,
de comparar em testes e impossível de alterar "por acidente" em tempo de execução.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import httpx

JitterStrategy = Literal["full", "equal", "none"]

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE", "TRACE"})


@dataclass(frozen=True)
class RetryPolicy:
    """Quando e quanto esperar entre tentativas.

    ``max_attempts`` conta a tentativa original: 3 = 1 chamada + 2 retries.
    O atraso da tentativa *n* (1-based) antes do jitter é
    ``min(max_delay, base_delay * multiplier ** (n - 1))``.
    """

    max_attempts: int = 3
    base_delay: float = 0.1
    max_delay: float = 10.0
    multiplier: float = 2.0
    jitter: JitterStrategy = "full"
    retry_on_status: frozenset[int] = frozenset({429, 502, 503, 504})
    retry_methods: frozenset[str] = IDEMPOTENT_METHODS
    respect_retry_after: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts deve ser >= 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("atrasos não podem ser negativos")
        if self.multiplier < 1:
            raise ValueError("multiplier deve ser >= 1")
        if self.jitter not in ("full", "equal", "none"):
            raise ValueError(f"jitter inválido: {self.jitter!r}")


@dataclass(frozen=True)
class RateLimitConfig:
    """Token bucket: ``rate`` tokens/segundo, rajadas de até ``capacity``.

    ``max_wait`` limita quanto uma chamada aceita esperar por um token;
    ``None`` = espera o quanto for preciso.
    """

    rate: float
    capacity: float
    max_wait: float | None = None

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError("rate deve ser > 0")
        if self.capacity < 1:
            raise ValueError("capacity deve ser >= 1")


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """Abre após ``failure_threshold`` falhas *consecutivas*.

    Depois de ``recovery_timeout`` segundos aberto, passa a HALF_OPEN e deixa
    passar até ``half_open_max_calls`` chamadas de teste.
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 1
    failure_status_codes: frozenset[int] = field(
        default_factory=lambda: frozenset(range(500, 600))
    )

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold deve ser >= 1")
        if self.recovery_timeout < 0:
            raise ValueError("recovery_timeout não pode ser negativo")
        if self.half_open_max_calls < 1:
            raise ValueError("half_open_max_calls deve ser >= 1")


@dataclass(frozen=True)
class TimeoutConfig:
    """Timeouts por fase (repassados ao httpx) + um prazo total opcional.

    ``total`` é um orçamento para a operação inteira, incluindo retries e
    esperas: nenhum retry é agendado se ultrapassaria o prazo.
    """

    connect: float | None = 5.0
    read: float | None = 10.0
    write: float | None = 10.0
    pool: float | None = 5.0
    total: float | None = None

    def to_httpx(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect, read=self.read, write=self.write, pool=self.pool
        )
