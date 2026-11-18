"""Rate limiting por token bucket, no estilo *reserva*.

Em vez de "esperar até ter token e então pegar", ``reserve()`` pega o token
imediatamente (o saldo pode ficar negativo) e devolve quanto tempo o chamador
precisa esperar. Vantagens:

* a seção crítica não contém ``sleep`` — o mesmo bucket serve código sync e
  async (o lock nunca é mantido enquanto se espera);
* chamadas concorrentes recebem esperas crescentes, na ordem de chegada (FIFO
  natural), sem "manada" disputando o próximo token.
"""

from __future__ import annotations

import threading

from .clock import Clock
from .config import RateLimitConfig
from .errors import RateLimitExceeded


class TokenBucket:
    def __init__(self, config: RateLimitConfig, clock: Clock) -> None:
        self.config = config
        self._clock = clock
        self._tokens = float(config.capacity)
        self._last = clock.monotonic()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(
                self.config.capacity, self._tokens + elapsed * self.config.rate
            )
            self._last = now

    @property
    def available(self) -> float:
        """Saldo atual (negativo = há reservas aguardando)."""
        with self._lock:
            self._refill(self._clock.monotonic())
            return self._tokens

    def reserve(self) -> float:
        """Reserva 1 token; devolve os segundos a esperar antes de usá-lo.

        Lança ``RateLimitExceeded`` (sem consumir o token) se a espera
        ultrapassar ``config.max_wait``.
        """
        with self._lock:
            self._refill(self._clock.monotonic())
            wait = 0.0 if self._tokens >= 1 else (1 - self._tokens) / self.config.rate
            max_wait = self.config.max_wait
            if max_wait is not None and wait > max_wait:
                raise RateLimitExceeded(wait)
            self._tokens -= 1
            return wait
