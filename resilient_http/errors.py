"""Exceções próprias. Todas herdam de ``ResilienceError`` para captura única."""

from __future__ import annotations


class ResilienceError(Exception):
    """Base de todas as falhas geradas pelas políticas (não pela rede)."""


class CircuitOpenError(ResilienceError):
    """O circuito está aberto: a chamada foi rejeitada sem tocar a rede."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"circuito aberto; tente novamente em {retry_after:.2f}s")


class RateLimitExceeded(ResilienceError):
    """A espera por um token excederia ``RateLimitConfig.max_wait``."""

    def __init__(self, wait: float) -> None:
        self.wait = wait
        super().__init__(f"rate limit: seria necessário esperar {wait:.2f}s")
