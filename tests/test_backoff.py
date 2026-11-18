import random

import pytest

from resilient_http.backoff import apply_jitter, compute_delay, exponential_delay
from resilient_http.config import RetryPolicy


def test_exponential_growth_and_cap():
    p = RetryPolicy(base_delay=0.5, multiplier=2, max_delay=3)
    assert [exponential_delay(n, p) for n in range(1, 6)] == [0.5, 1, 2, 3, 3]


def test_huge_attempt_does_not_overflow():
    p = RetryPolicy(base_delay=1, multiplier=10, max_delay=60)
    assert exponential_delay(10_000, p) == 60


def test_none_jitter_is_identity():
    assert apply_jitter(2.0, "none", random.Random(0)) == 2.0


@pytest.mark.parametrize("seed", range(50))
def test_full_jitter_bounds(seed):
    value = apply_jitter(2.0, "full", random.Random(seed))
    assert 0.0 <= value <= 2.0


@pytest.mark.parametrize("seed", range(50))
def test_equal_jitter_keeps_half(seed):
    value = apply_jitter(2.0, "equal", random.Random(seed))
    assert 1.0 <= value <= 2.0


def test_jitter_spreads_values():
    rng = random.Random(42)
    values = {round(apply_jitter(1.0, "full", rng), 6) for _ in range(20)}
    assert len(values) > 15  # não está devolvendo constante


def test_jitter_is_deterministic_with_seed():
    p = RetryPolicy(jitter="full")
    a = [compute_delay(n, p, random.Random(7)) for n in range(1, 4)]
    b = [compute_delay(n, p, random.Random(7)) for n in range(1, 4)]
    assert a == b


def test_invalid_policy_rejected():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(jitter="weird")  # type: ignore[arg-type]
