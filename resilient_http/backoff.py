"""Cálculo do atraso entre tentativas: backoff exponencial + jitter."""

from __future__ import annotations

import random

from .config import JitterStrategy, RetryPolicy


def exponential_delay(attempt: int, policy: RetryPolicy) -> float:
    """Atraso base (sem jitter) depois da tentativa ``attempt`` (1-based)."""
    if attempt < 1:
        raise ValueError("attempt é 1-based")
    # Limitamos o expoente antes de calcular para não estourar float em
    # políticas com muitos retries.
    raw = policy.base_delay * policy.multiplier ** min(attempt - 1, 64)
    return min(policy.max_delay, raw)


def apply_jitter(delay: float, strategy: JitterStrategy, rng: random.Random) -> float:
    """Espalha ``delay`` aleatoriamente para evitar retries sincronizados.

    - ``"none"``:  devolve ``delay`` sem mudança.
    - ``"full"``:  uniforme em ``[0, delay]``.
    - ``"equal"``: metade fixa + metade aleatória: ``delay/2 + U[0, delay/2]``.
    """
    if strategy == "none":
        return delay
    if strategy == "full":
        return rng.uniform(0.0, delay)
    if strategy == "equal":
        half = delay / 2
        return half + rng.uniform(0.0, half)
    raise ValueError(f"estratégia de jitter desconhecida: {strategy!r}")


def compute_delay(attempt: int, policy: RetryPolicy, rng: random.Random) -> float:
    return apply_jitter(exponential_delay(attempt, policy), policy.jitter, rng)
