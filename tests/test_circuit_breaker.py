import pytest

from resilient_http import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    CircuitState,
    FakeClock,
)


def make(clock, transitions=None, **kw):
    cfg = CircuitBreakerConfig(**{"failure_threshold": 3, "recovery_timeout": 10, **kw})
    cb = transitions.append if transitions is not None else None
    return CircuitBreaker(cfg, clock, on_state_change=(lambda o, n: cb((o, n))) if cb else None)


def fail(breaker, n):
    for _ in range(n):
        breaker.before_call()
        breaker.record_failure()


def test_opens_after_consecutive_failures(clock):
    b = make(clock)
    fail(b, 2)
    assert b.state is CircuitState.CLOSED
    fail(b, 1)
    assert b.state is CircuitState.OPEN


def test_success_resets_consecutive_count(clock):
    b = make(clock)
    fail(b, 2)
    b.before_call(); b.record_success()
    fail(b, 2)
    assert b.state is CircuitState.CLOSED


def test_open_rejects_with_remaining_time(clock):
    b = make(clock)
    fail(b, 3)
    clock.advance(4)
    with pytest.raises(CircuitOpenError) as info:
        b.before_call()
    assert info.value.retry_after == pytest.approx(6)


def test_half_open_after_timeout_then_close_on_success(clock):
    t = []
    b = make(clock, t)
    fail(b, 3)
    clock.advance(10)
    assert b.state is CircuitState.HALF_OPEN
    b.before_call(); b.record_success()
    assert b.state is CircuitState.CLOSED
    assert t == [
        (CircuitState.CLOSED, CircuitState.OPEN),
        (CircuitState.OPEN, CircuitState.HALF_OPEN),
        (CircuitState.HALF_OPEN, CircuitState.CLOSED),
    ]


def test_half_open_failure_reopens_and_restarts_timer(clock):
    b = make(clock)
    fail(b, 3)
    clock.advance(10)
    b.before_call(); b.record_failure()
    assert b.state is CircuitState.OPEN
    clock.advance(9.9)
    assert b.state is CircuitState.OPEN


def test_half_open_limits_concurrent_probes(clock):
    b = make(clock, half_open_max_calls=1)
    fail(b, 3)
    clock.advance(10)
    b.before_call()
    with pytest.raises(CircuitOpenError):
        b.before_call()
    b.release()
    b.before_call()  # vaga liberada


def test_late_failure_while_open_is_ignored(clock):
    b = make(clock)
    fail(b, 3)
    b.record_failure()
    clock.advance(10)
    assert b.state is CircuitState.HALF_OPEN
