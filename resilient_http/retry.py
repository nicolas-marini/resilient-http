"""Decisão de retry, sem I/O.

``RetryController`` recebe o resultado de uma tentativa e responde *se* e
*quanto* esperar. Não dorme nem chama a rede — por isso as fachadas sync e
async reutilizam exatamente a mesma lógica.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from .backoff import compute_delay
from .clock import Clock
from .config import RetryPolicy

# Erros em que a requisição comprovadamente NÃO chegou ao servidor: é seguro
# repetir até métodos não idempotentes (POST/PATCH).
_NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay: float = 0.0
    reason: str = ""


def parse_retry_after(value: str | None) -> float | None:
    """Interpreta ``Retry-After`` em segundos ou como data HTTP."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


class RetryController:
    """Estado de retry de UMA operação lógica (todas as suas tentativas)."""

    def __init__(
        self,
        policy: RetryPolicy,
        method: str,
        clock: Clock,
        rng: random.Random,
        deadline: float | None = None,
    ) -> None:
        self.policy = policy
        self.method = method.upper()
        self.deadline = deadline
        self._clock = clock
        self._rng = rng

    def decide(
        self,
        attempt: int,
        *,
        response: httpx.Response | None = None,
        error: BaseException | None = None,
    ) -> RetryDecision:
        p = self.policy

        if error is not None:
            if not isinstance(error, httpx.TransportError):
                return RetryDecision(False, reason="erro não recuperável")
            safe = self.method in p.retry_methods or isinstance(error, _NOT_SENT_ERRORS)
            reason = type(error).__name__
        elif response is not None:
            if response.status_code not in p.retry_on_status:
                return RetryDecision(False, reason="status final")
            safe = self.method in p.retry_methods
            reason = f"HTTP {response.status_code}"
        else:  # pragma: no cover - uso incorreto
            raise TypeError("informe response ou error")

        if not safe:
            return RetryDecision(False, reason=f"{self.method} não é idempotente")
        if attempt >= p.max_attempts:
            return RetryDecision(False, reason="tentativas esgotadas")

        delay = compute_delay(attempt, p, self._rng)
        if response is not None and p.respect_retry_after:
            server_wait = parse_retry_after(response.headers.get("Retry-After"))
            if server_wait is not None:
                if server_wait > p.max_delay:
                    return RetryDecision(False, reason="Retry-After acima de max_delay")
                delay = max(delay, server_wait)

        if self.deadline is not None and self._clock.monotonic() + delay > self.deadline:
            return RetryDecision(False, reason="prazo total estouraria")
        return RetryDecision(True, delay, reason)
