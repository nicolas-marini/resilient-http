"""Fachadas sync e async sobre ``httpx``.

Toda a lógica de resiliência vive em ``_ResilienceCore``; as subclasses só
fazem o I/O (chamar o httpx e dormir). Ordem de cada tentativa:

    1. token bucket  → espera, se preciso
    2. circuit breaker → pode rejeitar sem tocar a rede
    3. requisição httpx (com timeouts por fase, limitados pelo prazo total)
    4. registra resultado no breaker
    5. RetryController decide: devolver, lançar ou esperar e repetir
"""

from __future__ import annotations

import random
from typing import Any

import httpx

from .circuit_breaker import CircuitBreaker, CircuitState
from .clock import Clock, SystemClock
from .config import CircuitBreakerConfig, RateLimitConfig, RetryPolicy, TimeoutConfig
from .errors import CircuitOpenError, ResilienceError
from .events import Event, EventHandler, EventKind
from .rate_limiter import TokenBucket
from .retry import RetryController, RetryDecision

_DEFAULT_BREAKER = CircuitBreakerConfig()


class DeadlineExceeded(ResilienceError):
    """O prazo total (``TimeoutConfig.total``) acabou antes de nova tentativa."""


class _ResilienceCore:
    def __init__(
        self,
        *,
        retry: RetryPolicy | None,
        rate_limit: RateLimitConfig | None,
        circuit_breaker: CircuitBreakerConfig | None,
        timeout: TimeoutConfig | None,
        clock: Clock | None,
        rng: random.Random | None,
        on_event: EventHandler | None,
    ) -> None:
        self.retry_policy = retry or RetryPolicy()
        self.timeout_config = timeout or TimeoutConfig()
        self._clock: Clock = clock or SystemClock()
        self._rng = rng or random.Random()
        self._on_event = on_event
        self.rate_limiter = TokenBucket(rate_limit, self._clock) if rate_limit else None
        self.circuit_breaker = (
            CircuitBreaker(circuit_breaker, self._clock, self._on_circuit_change)
            if circuit_breaker
            else None
        )

    # ------------------------------------------------------------ eventos
    def _emit(self, kind: EventKind, **data: Any) -> None:
        if self._on_event is not None:
            self._on_event(Event(kind, self._clock.monotonic(), data))

    def _on_circuit_change(self, old: CircuitState, new: CircuitState) -> None:
        self._emit("circuit_state", old=old.value, new=new.value)

    # ------------------------------------------------------------ etapas
    def _new_controller(self, method: str) -> RetryController:
        total = self.timeout_config.total
        deadline = self._clock.monotonic() + total if total is not None else None
        return RetryController(self.retry_policy, method, self._clock, self._rng, deadline)

    def _token_wait(self) -> float:
        if self.rate_limiter is None:
            return 0.0
        wait = self.rate_limiter.reserve()
        if wait > 0:
            self._emit("rate_limited", wait=wait)
        return wait

    def _enter(self, ctrl: RetryController, method: str, url: Any, attempt: int,
               kwargs: dict[str, Any]) -> dict[str, Any]:
        """Passa pelo breaker e prepara kwargs da tentativa (timeout ajustado)."""
        if ctrl.deadline is not None and self._clock.monotonic() >= ctrl.deadline:
            raise DeadlineExceeded("prazo total esgotado")
        if self.circuit_breaker is not None:
            try:
                self.circuit_breaker.before_call()
            except CircuitOpenError as exc:
                self._emit("circuit_rejected", method=method, url=str(url),
                           retry_after=exc.retry_after)
                raise
        self._emit("attempt", method=method, url=str(url), attempt=attempt)
        if ctrl.deadline is not None and "timeout" not in kwargs:
            kwargs = {**kwargs, "timeout": self._bounded_timeout(ctrl.deadline)}
        return kwargs

    def _bounded_timeout(self, deadline: float) -> httpx.Timeout:
        remaining = max(0.001, deadline - self._clock.monotonic())
        t = self.timeout_config

        def cap(v: float | None) -> float:
            return remaining if v is None else min(v, remaining)

        return httpx.Timeout(connect=cap(t.connect), read=cap(t.read),
                             write=cap(t.write), pool=cap(t.pool))

    def _after_response(self, ctrl: RetryController, attempt: int,
                        response: httpx.Response) -> RetryDecision:
        cb = self.circuit_breaker
        if cb is not None:
            if response.status_code in cb.config.failure_status_codes:
                cb.record_failure()
            else:
                cb.record_success()
        self._emit("response", status=response.status_code, attempt=attempt)
        decision = ctrl.decide(attempt, response=response)
        self._report(decision, attempt, retryable=response.status_code
                     in self.retry_policy.retry_on_status)
        return decision

    def _after_error(self, ctrl: RetryController, attempt: int,
                     error: httpx.TransportError) -> RetryDecision:
        if self.circuit_breaker is not None:
            self.circuit_breaker.record_failure()
        self._emit("error", error=type(error).__name__, message=str(error), attempt=attempt)
        decision = ctrl.decide(attempt, error=error)
        self._report(decision, attempt, retryable=True)
        return decision

    def _report(self, decision: RetryDecision, attempt: int, retryable: bool) -> None:
        if decision.retry:
            self._emit("retry_scheduled", attempt=attempt, delay=decision.delay,
                       reason=decision.reason)
        elif retryable:
            self._emit("gave_up", attempt=attempt, reason=decision.reason)

    def _release(self) -> None:
        if self.circuit_breaker is not None:
            self.circuit_breaker.release()


class ResilientClient(_ResilienceCore):
    """Cliente síncrono. Use como context manager ou chame ``close()``."""

    def __init__(
        self,
        base_url: str | httpx.URL = "",
        *,
        retry: RetryPolicy | None = None,
        rate_limit: RateLimitConfig | None = None,
        circuit_breaker: CircuitBreakerConfig | None = _DEFAULT_BREAKER,
        timeout: TimeoutConfig | None = None,
        clock: Clock | None = None,
        rng: random.Random | None = None,
        on_event: EventHandler | None = None,
        transport: httpx.BaseTransport | None = None,
        **httpx_kwargs: Any,
    ) -> None:
        super().__init__(retry=retry, rate_limit=rate_limit, circuit_breaker=circuit_breaker,
                         timeout=timeout, clock=clock, rng=rng, on_event=on_event)
        self._http = httpx.Client(base_url=base_url, timeout=self.timeout_config.to_httpx(),
                                  transport=transport, **httpx_kwargs)

    def request(self, method: str, url: str | httpx.URL, **kwargs: Any) -> httpx.Response:
        ctrl = self._new_controller(method)
        attempt = 0
        while True:
            attempt += 1
            if (wait := self._token_wait()) > 0:
                self._clock.sleep(wait)
            call_kwargs = self._enter(ctrl, method, url, attempt, kwargs)
            try:
                response = self._http.request(method, url, **call_kwargs)
            except httpx.TransportError as exc:
                decision = self._after_error(ctrl, attempt, exc)
                if not decision.retry:
                    raise
            except BaseException:
                self._release()
                raise
            else:
                decision = self._after_response(ctrl, attempt, response)
                if not decision.retry:
                    return response
                response.close()
            self._clock.sleep(decision.delay)

    def get(self, url: str | httpx.URL, **kw: Any) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str | httpx.URL, **kw: Any) -> httpx.Response:
        return self.request("POST", url, **kw)

    def put(self, url: str | httpx.URL, **kw: Any) -> httpx.Response:
        return self.request("PUT", url, **kw)

    def patch(self, url: str | httpx.URL, **kw: Any) -> httpx.Response:
        return self.request("PATCH", url, **kw)

    def delete(self, url: str | httpx.URL, **kw: Any) -> httpx.Response:
        return self.request("DELETE", url, **kw)

    def head(self, url: str | httpx.URL, **kw: Any) -> httpx.Response:
        return self.request("HEAD", url, **kw)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ResilientClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class AsyncResilientClient(_ResilienceCore):
    """Cliente assíncrono; mesma semântica de ``ResilientClient``."""

    def __init__(
        self,
        base_url: str | httpx.URL = "",
        *,
        retry: RetryPolicy | None = None,
        rate_limit: RateLimitConfig | None = None,
        circuit_breaker: CircuitBreakerConfig | None = _DEFAULT_BREAKER,
        timeout: TimeoutConfig | None = None,
        clock: Clock | None = None,
        rng: random.Random | None = None,
        on_event: EventHandler | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        **httpx_kwargs: Any,
    ) -> None:
        super().__init__(retry=retry, rate_limit=rate_limit, circuit_breaker=circuit_breaker,
                         timeout=timeout, clock=clock, rng=rng, on_event=on_event)
        self._http = httpx.AsyncClient(base_url=base_url,
                                       timeout=self.timeout_config.to_httpx(),
                                       transport=transport, **httpx_kwargs)

    async def request(self, method: str, url: str | httpx.URL, **kwargs: Any) -> httpx.Response:
        ctrl = self._new_controller(method)
        attempt = 0
        while True:
            attempt += 1
            if (wait := self._token_wait()) > 0:
                await self._clock.asleep(wait)
            call_kwargs = self._enter(ctrl, method, url, attempt, kwargs)
            try:
                response = await self._http.request(method, url, **call_kwargs)
            except httpx.TransportError as exc:
                decision = self._after_error(ctrl, attempt, exc)
                if not decision.retry:
                    raise
            except BaseException:
                self._release()
                raise
            else:
                decision = self._after_response(ctrl, attempt, response)
                if not decision.retry:
                    return response
                await response.aclose()
            await self._clock.asleep(decision.delay)

    async def get(self, url: str | httpx.URL, **kw: Any) -> httpx.Response:
        return await self.request("GET", url, **kw)

    async def post(self, url: str | httpx.URL, **kw: Any) -> httpx.Response:
        return await self.request("POST", url, **kw)

    async def put(self, url: str | httpx.URL, **kw: Any) -> httpx.Response:
        return await self.request("PUT", url, **kw)

    async def patch(self, url: str | httpx.URL, **kw: Any) -> httpx.Response:
        return await self.request("PATCH", url, **kw)

    async def delete(self, url: str | httpx.URL, **kw: Any) -> httpx.Response:
        return await self.request("DELETE", url, **kw)

    async def head(self, url: str | httpx.URL, **kw: Any) -> httpx.Response:
        return await self.request("HEAD", url, **kw)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncResilientClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
