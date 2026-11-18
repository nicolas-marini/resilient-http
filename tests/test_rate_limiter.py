import pytest

from resilient_http import FakeClock, RateLimitConfig, RateLimitExceeded, TokenBucket


def test_burst_up_to_capacity_is_free(clock: FakeClock):
    bucket = TokenBucket(RateLimitConfig(rate=1, capacity=3), clock)
    assert [bucket.reserve() for _ in range(3)] == [0, 0, 0]


def test_waits_grow_fifo_when_empty(clock: FakeClock):
    bucket = TokenBucket(RateLimitConfig(rate=2, capacity=1), clock)
    assert bucket.reserve() == 0
    assert bucket.reserve() == pytest.approx(0.5)
    assert bucket.reserve() == pytest.approx(1.0)


def test_refill_over_time_respects_capacity(clock: FakeClock):
    bucket = TokenBucket(RateLimitConfig(rate=10, capacity=2), clock)
    bucket.reserve(); bucket.reserve()
    clock.advance(0.1)
    assert bucket.available == pytest.approx(1)
    clock.advance(100)
    assert bucket.available == pytest.approx(2)


def test_reservation_is_honoured_after_waiting(clock: FakeClock):
    bucket = TokenBucket(RateLimitConfig(rate=1, capacity=1), clock)
    bucket.reserve()
    wait = bucket.reserve()
    clock.advance(wait)
    assert bucket.available == pytest.approx(0)


def test_max_wait_raises_without_consuming(clock: FakeClock):
    bucket = TokenBucket(RateLimitConfig(rate=1, capacity=1, max_wait=0.5), clock)
    bucket.reserve()
    with pytest.raises(RateLimitExceeded) as info:
        bucket.reserve()
    assert info.value.wait == pytest.approx(1.0)
    assert bucket.available == pytest.approx(0)  # nada foi debitado


def test_invalid_config():
    with pytest.raises(ValueError):
        RateLimitConfig(rate=0, capacity=1)
