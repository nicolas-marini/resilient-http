"""Testes de integração do cliente: rede via MockTransport, tempo via FakeClock."""

from __future__ import annotations

import httpx
import pytest

from resilient_http import (
    AsyncResilientClient,
    CircuitBreakerConfig,
    CircuitOpenError,
    CircuitState,
    DeadlineExceeded,
    RateLimitConfig,
    ResilientClient,
    RetryPolicy,
    TimeoutConfig,
)

NO_JITTER = RetryPolicy(max_attempts=4, base_delay=1, multiplier=2, max_delay=30, jitter="none")


def connect_error() -> httpx.ConnectError:
    return httpx.ConnectError("recusada")


def make_client(server, clock, rng, **kw) -> ResilientClient:
    kw.setdefault("retry", NO_JITTER)
    return ResilientClient("http://api.test", transport=httpx.MockTransport(server),
                           clock=clock, rng=rng, **kw)


# ---------------------------------------------------------------- retry
def test_success_first_try_no_sleep(server_factory, clock, rng):
    server = server_factory([200])
    with make_client(server, clock, rng) as c:
        assert c.get("/x").status_code == 200
    assert server.calls == 1 and clock.sleeps == []


def test_retries_503_with_exponential_backoff(server_factory, clock, rng):
    server = server_factory([503, 503, 200])
    with make_client(server, clock, rng) as c:
        assert c.get("/x").status_code == 200
    assert server.calls == 3
    assert clock.sleeps == [1, 2]


def test_returns_last_response_when_attempts_exhausted(server_factory, clock, rng):
    server = server_factory([503])
    with make_client(server, clock, rng, circuit_breaker=None) as c:
        assert c.get("/x").status_code == 503
    assert server.calls == 4
    assert clock.sleeps == [1, 2, 4]


def test_transport_error_reraised_after_retries(server_factory, clock, rng):
    server = server_factory([httpx.ReadTimeout("lento")])
    with make_client(server, clock, rng, circuit_breaker=None) as c:
        with pytest.raises(httpx.ReadTimeout):
            c.get("/x")
    assert server.calls == 4


def test_non_retryable_status_is_returned_immediately(server_factory, clock, rng):
    server = server_factory([404])
    with make_client(server, clock, rng) as c:
        assert c.get("/x").status_code == 404
    assert server.calls == 1


def test_post_not_retried_on_503(server_factory, clock, rng):
    server = server_factory([503, 200])
    with make_client(server, clock, rng) as c:
        assert c.post("/x", json={}).status_code == 503
    assert server.calls == 1


def test_post_retried_when_request_never_left(server_factory, clock, rng):
    """ConnectError = nada chegou ao servidor; repetir POST é seguro."""
    server = server_factory([connect_error(), 201])
    with make_client(server, clock, rng) as c:
        assert c.post("/x", json={}).status_code == 201


def test_retry_after_header_overrides_shorter_backoff(server_factory, clock, rng):
    server = server_factory([httpx.Response(429, headers={"Retry-After": "7"}), 200])
    with make_client(server, clock, rng) as c:
        assert c.get("/x").status_code == 200
    assert clock.sleeps == [7]


def test_retry_after_above_max_delay_gives_up(server_factory, clock, rng):
    server = server_factory([httpx.Response(429, headers={"Retry-After": "3600"}), 200])
    with make_client(server, clock, rng) as c:
        assert c.get("/x").status_code == 429
    assert server.calls == 1


# ---------------------------------------------------------------- timeouts
def test_total_deadline_stops_retrying(server_factory, clock, rng):
    server = server_factory([503])
    with make_client(server, clock, rng, timeout=TimeoutConfig(total=2.5)) as c:
        assert c.get("/x").status_code == 503
    # esperas 1 (t=1) e 2 (t=3 > 2.5) -> só a primeira cabe
    assert server.calls == 2 and clock.sleeps == [1]


def test_per_attempt_timeout_bounded_by_deadline(clock, rng):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200)

    cfg = TimeoutConfig(connect=5, read=10, write=10, pool=5, total=3)
    with make_client(handler, clock, rng, timeout=cfg) as c:
        c.get("/x")
    assert seen[0] == {"connect": 3, "read": 3, "write": 3, "pool": 3}


def test_phase_timeouts_passed_to_httpx(clock, rng):
    seen: list[dict] = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200)

    with make_client(handler, clock, rng, timeout=TimeoutConfig(connect=1, read=2, write=3, pool=4)) as c:
        c.get("/x")
    assert seen[0] == {"connect": 1, "read": 2, "write": 3, "pool": 4}


def test_deadline_exceeded_when_rate_limit_wait_consumes_budget(server_factory, clock, rng):
    server = server_factory([200])
    with make_client(server, clock, rng, timeout=TimeoutConfig(total=1),
                     rate_limit=RateLimitConfig(rate=0.5, capacity=1)) as c:
        c.get("/x")
        with pytest.raises(DeadlineExceeded):
            c.get("/x")  # precisa esperar 2s por token


# ---------------------------------------------------------------- breaker
def test_breaker_opens_and_short_circuits(server_factory, clock, rng):
    server = server_factory([connect_error()])
    cb = CircuitBreakerConfig(failure_threshold=3, recovery_timeout=60)
    with make_client(server, clock, rng, circuit_breaker=cb,
                     retry=RetryPolicy(max_attempts=1)) as c:
        for _ in range(3):
            with pytest.raises(httpx.ConnectError):
                c.get("/x")
        with pytest.raises(CircuitOpenError):
            c.get("/x")
    assert server.calls == 3  # 4ª chamada nem saiu


def test_breaker_open_mid_retry_stops_loop(server_factory, clock, rng):
    server = server_factory([503])
    cb = CircuitBreakerConfig(failure_threshold=2, recovery_timeout=60)
    with make_client(server, clock, rng, circuit_breaker=cb) as c:
        with pytest.raises(CircuitOpenError):
            c.get("/x")
    assert server.calls == 2


def test_breaker_recovers_via_half_open(server_factory, clock, rng):
    server = server_factory([503, 503, 200])
    cb = CircuitBreakerConfig(failure_threshold=2, recovery_timeout=30)
    with make_client(server, clock, rng, circuit_breaker=cb,
                     retry=RetryPolicy(max_attempts=1)) as c:
        c.get("/x"); c.get("/x")
        assert c.circuit_breaker.state is CircuitState.OPEN
        clock.advance(30)
        assert c.get("/x").status_code == 200
        assert c.circuit_breaker.state is CircuitState.CLOSED


def test_4xx_does_not_count_as_breaker_failure(server_factory, clock, rng):
    server = server_factory([404])
    cb = CircuitBreakerConfig(failure_threshold=1)
    with make_client(server, clock, rng, circuit_breaker=cb) as c:
        for _ in range(5):
            c.get("/x")
        assert c.circuit_breaker.state is CircuitState.CLOSED


# ---------------------------------------------------------------- rate limit
def test_rate_limit_sleeps_between_bursts(server_factory, clock, rng):
    server = server_factory([200])
    with make_client(server, clock, rng, rate_limit=RateLimitConfig(rate=2, capacity=2)) as c:
        for _ in range(4):
            c.get("/x")
    assert clock.sleeps == [0.5, 0.5]


# ---------------------------------------------------------------- eventos
def test_events_tell_the_story(server_factory, clock, rng):
    events = []
    server = server_factory([503, 200])
    with make_client(server, clock, rng, on_event=events.append) as c:
        c.get("/x")
    assert [e.kind for e in events] == [
        "attempt", "response", "retry_scheduled", "attempt", "response",
    ]
    assert events[2].data["delay"] == 1
    assert events[3].time == 1


# ---------------------------------------------------------------- async
async def test_async_client_same_semantics(server_factory, clock, rng):
    server = server_factory([connect_error(), 503, 200])
    async with AsyncResilientClient(
        "http://api.test", transport=httpx.MockTransport(server),
        clock=clock, rng=rng, retry=NO_JITTER,
    ) as c:
        r = await c.get("/x")
    assert r.status_code == 200
    assert clock.sleeps == [1, 2]


async def test_async_breaker_rejects(server_factory, clock, rng):
    server = server_factory([500])
    async with AsyncResilientClient(
        "http://api.test", transport=httpx.MockTransport(server), clock=clock, rng=rng,
        retry=RetryPolicy(max_attempts=1),
        circuit_breaker=CircuitBreakerConfig(failure_threshold=1),
    ) as c:
        await c.get("/x")
        with pytest.raises(CircuitOpenError):
            await c.get("/x")
