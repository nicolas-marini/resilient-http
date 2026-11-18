"""resilient_http — wrapper resiliente sobre httpx."""

from .circuit_breaker import CircuitBreaker, CircuitState
from .client import AsyncResilientClient, DeadlineExceeded, ResilientClient
from .clock import Clock, FakeClock, SystemClock
from .config import CircuitBreakerConfig, RateLimitConfig, RetryPolicy, TimeoutConfig
from .errors import CircuitOpenError, RateLimitExceeded, ResilienceError
from .events import Event
from .rate_limiter import TokenBucket
from .retry import RetryController, RetryDecision

__all__ = [
    "AsyncResilientClient",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitOpenError",
    "CircuitState",
    "Clock",
    "DeadlineExceeded",
    "Event",
    "FakeClock",
    "RateLimitConfig",
    "RateLimitExceeded",
    "ResilienceError",
    "ResilientClient",
    "RetryController",
    "RetryDecision",
    "RetryPolicy",
    "SystemClock",
    "TimeoutConfig",
    "TokenBucket",
]
