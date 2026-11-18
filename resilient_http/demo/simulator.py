"""Simulação determinística: o cliente REAL contra um servidor instável falso.

Tempo via ``FakeClock`` (60s simulados rodam em milissegundos) e rede via
``httpx.MockTransport``. Mesma semente ⇒ mesmo resultado.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

import httpx

from ..client import DeadlineExceeded, ResilientClient
from ..clock import FakeClock
from ..config import CircuitBreakerConfig, RateLimitConfig, RetryPolicy, TimeoutConfig
from ..errors import CircuitOpenError, RateLimitExceeded
from ..events import Event


@dataclass
class Scenario:
    # carga
    requests: int = 60
    interval: float = 0.5
    # servidor
    latency: float = 0.05
    failure_rate: float = 0.1
    outage_start: float = 8.0
    outage_end: float = 18.0
    failure_mode: Literal["503", "connect", "timeout"] = "503"
    # retry
    max_attempts: int = 4
    base_delay: float = 0.2
    max_delay: float = 5.0
    multiplier: float = 2.0
    jitter: Literal["full", "equal", "none"] = "full"
    # rate limit (rate <= 0 desliga)
    rate: float = 4.0
    capacity: float = 5.0
    # breaker (threshold <= 0 desliga)
    failure_threshold: int = 5
    recovery_timeout: float = 4.0
    # prazo total por operação (<= 0 desliga)
    total_timeout: float = 0.0
    seed: int = 7

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scenario":
        s = cls()
        for f in fields(cls):
            if f.name in data and data[f.name] is not None:
                default = getattr(s, f.name)
                value = data[f.name]
                setattr(s, f.name, type(default)(value) if not isinstance(default, str) else str(value))
        s.requests = max(1, min(s.requests, 500))
        s.max_attempts = max(1, min(s.max_attempts, 10))
        return s


def simulate(scenario: Scenario) -> dict[str, Any]:
    s = scenario
    clock = FakeClock()
    server_rng = random.Random(s.seed * 7919 + 1)
    current: dict[str, Any] = {"id": None}
    events: list[dict[str, Any]] = []
    client: ResilientClient

    def handler(request: httpx.Request) -> httpx.Response:
        now = clock.monotonic()
        clock.advance(s.latency)
        down = s.outage_start <= now < s.outage_end or server_rng.random() < s.failure_rate
        if not down:
            return httpx.Response(200, json={"ok": True})
        if s.failure_mode == "connect":
            raise httpx.ConnectError("conexão recusada", request=request)
        if s.failure_mode == "timeout":
            clock.advance(1.0)
            raise httpx.ReadTimeout("leitura expirou", request=request)
        return httpx.Response(503, json={"ok": False})

    def on_event(e: Event) -> None:
        item = {"kind": e.kind, "t": round(e.time, 4), "req": current["id"], **e.data}
        if e.kind == "attempt" and client.rate_limiter is not None:
            item["tokens"] = round(client.rate_limiter.available, 3)
        events.append(item)

    client = ResilientClient(
        "http://sim.local",
        transport=httpx.MockTransport(handler),
        clock=clock,
        rng=random.Random(s.seed),
        on_event=on_event,
        retry=RetryPolicy(max_attempts=s.max_attempts, base_delay=s.base_delay,
                          max_delay=s.max_delay, multiplier=max(1.0, s.multiplier),
                          jitter=s.jitter),
        rate_limit=RateLimitConfig(rate=s.rate, capacity=max(1.0, s.capacity))
        if s.rate > 0 else None,
        circuit_breaker=CircuitBreakerConfig(failure_threshold=s.failure_threshold,
                                             recovery_timeout=s.recovery_timeout)
        if s.failure_threshold > 0 else None,
        timeout=TimeoutConfig(total=s.total_timeout if s.total_timeout > 0 else None),
    )

    results: list[dict[str, Any]] = []
    with client:
        for i in range(s.requests):
            scheduled = i * s.interval
            if clock.monotonic() < scheduled:
                clock.set(scheduled)
            current["id"] = i
            start = clock.monotonic()
            n_before = sum(1 for e in events if e["kind"] == "attempt")
            try:
                status = client.get(f"/item/{i}").status_code
                outcome = "ok" if status < 400 else "http_error"
            except CircuitOpenError:
                status, outcome = None, "rejected"
            except (RateLimitExceeded, DeadlineExceeded):
                status, outcome = None, "deadline"
            except httpx.TransportError:
                status, outcome = None, "network_error"
            attempts = sum(1 for e in events if e["kind"] == "attempt") - n_before
            results.append({
                "id": i, "scheduled": round(scheduled, 4), "start": round(start, 4),
                "end": round(clock.monotonic(), 4), "status": status,
                "outcome": outcome, "attempts": attempts,
            })

    counts: dict[str, int] = {}
    for r in results:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    total_attempts = sum(r["attempts"] for r in results)
    return {
        "scenario": asdict(s),
        "duration": round(clock.monotonic(), 4),
        "requests": results,
        "events": events,
        "summary": {
            "requests": len(results),
            "outcomes": counts,
            "network_calls": total_attempts,
            "retries": sum(1 for e in events if e["kind"] == "retry_scheduled"),
            "rejected_by_breaker": counts.get("rejected", 0),
            "rate_limit_wait": round(sum(e.get("wait", 0) for e in events
                                         if e["kind"] == "rate_limited"), 3),
            "breaker_trips": sum(1 for e in events
                                 if e["kind"] == "circuit_state" and e["new"] == "open"),
        },
    }
