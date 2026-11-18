"""Circuit breaker com três estados.

    CLOSED ──(N falhas consecutivas)──▶ OPEN
      ▲                                  │ (recovery_timeout)
      │                                  ▼
      └────────(sucesso)──────────── HALF_OPEN ──(falha)──▶ OPEN

A transição OPEN → HALF_OPEN é *preguiçosa*: acontece quando alguém consulta
o estado depois do prazo, sem threads nem timers em segundo plano.
"""

from __future__ import annotations

import threading
from enum import Enum
from typing import Callable

from .clock import Clock
from .config import CircuitBreakerConfig
from .errors import CircuitOpenError


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


StateChangeHandler = Callable[[CircuitState, CircuitState], None]


class CircuitBreaker:
    def __init__(
        self,
        config: CircuitBreakerConfig,
        clock: Clock,
        on_state_change: StateChangeHandler | None = None,
    ) -> None:
        self.config = config
        self._clock = clock
        self._on_state_change = on_state_change
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probes_in_flight = 0
        # RLock: o callback de mudança de estado pode consultar ``state``.
        self._lock = threading.RLock()

    # ------------------------------------------------------------ consulta
    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    # ------------------------------------------------------------ protocolo
    def before_call(self) -> None:
        """Pede permissão para uma chamada. Lança ``CircuitOpenError`` se negada."""
        with self._lock:
            self._maybe_half_open()
            if self._state is CircuitState.OPEN:
                remaining = self._opened_at + self.config.recovery_timeout
                raise CircuitOpenError(max(0.0, remaining - self._clock.monotonic()))
            if self._state is CircuitState.HALF_OPEN:
                if self._probes_in_flight >= self.config.half_open_max_calls:
                    raise CircuitOpenError(0.0)
                self._probes_in_flight += 1

    def record_success(self) -> None:
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._transition(CircuitState.CLOSED)
            elif self._state is CircuitState.CLOSED:
                self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._trip()
            elif self._state is CircuitState.CLOSED:
                self._failures += 1
                if self._failures >= self.config.failure_threshold:
                    self._trip()
            # OPEN: chamada admitida antes de abrir terminou agora; ignorar.

    def release(self) -> None:
        """Libera a permissão sem julgar o resultado (ex.: erro de programação)."""
        with self._lock:
            if self._state is CircuitState.HALF_OPEN and self._probes_in_flight:
                self._probes_in_flight -= 1

    def reset(self) -> None:
        with self._lock:
            self._transition(CircuitState.CLOSED)

    # ------------------------------------------------------------ interno
    def _maybe_half_open(self) -> None:
        if (
            self._state is CircuitState.OPEN
            and self._clock.monotonic() - self._opened_at >= self.config.recovery_timeout
        ):
            self._transition(CircuitState.HALF_OPEN)

    def _trip(self) -> None:
        self._opened_at = self._clock.monotonic()
        self._transition(CircuitState.OPEN)

    def _transition(self, new: CircuitState) -> None:
        old = self._state
        self._state = new
        self._probes_in_flight = 0
        if new is CircuitState.CLOSED:
            self._failures = 0
        if old is not new and self._on_state_change is not None:
            self._on_state_change(old, new)
