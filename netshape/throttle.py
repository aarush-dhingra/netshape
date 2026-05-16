"""Standalone throttling primitives used by the proxy."""

from __future__ import annotations

import random
import time
from collections.abc import Callable


class TokenBucket:
    """Token bucket measured in bits.

    A ``rate_bps`` of ``0`` means unlimited bandwidth.
    """

    def __init__(
        self,
        rate_bps: int,
        *,
        capacity_bits: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate_bps < 0:
            raise ValueError("rate_bps must be non-negative")
        if capacity_bits is not None and capacity_bits < 0:
            raise ValueError("capacity_bits must be non-negative")

        self.rate_bps = int(rate_bps)
        self.capacity_bits = int(capacity_bits if capacity_bits is not None else rate_bps)
        self._clock = clock
        self._tokens = float("inf") if self.rate_bps == 0 else float(self.capacity_bits)
        self._last_refill = self._clock()

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens

    def consume(self, byte_count: int) -> float:
        """Consume bytes and return required wait time in seconds."""

        if byte_count < 0:
            raise ValueError("byte_count must be non-negative")
        if self.rate_bps == 0 or byte_count == 0:
            return 0.0

        self._refill()
        bits_needed = byte_count * 8
        if self._tokens >= bits_needed:
            self._tokens -= bits_needed
            return 0.0

        shortage = bits_needed - self._tokens
        self._tokens = 0.0
        return shortage / self.rate_bps

    def reset_rate(self, rate_bps: int) -> None:
        """Update bandwidth rate while preserving current refill timing."""

        if rate_bps < 0:
            raise ValueError("rate_bps must be non-negative")
        self._refill()
        self.rate_bps = int(rate_bps)
        self.capacity_bits = int(rate_bps)
        self._tokens = float("inf") if rate_bps == 0 else min(self._tokens, self.capacity_bits)
        self._last_refill = self._clock()

    def _refill(self) -> None:
        if self.rate_bps == 0:
            self._tokens = float("inf")
            self._last_refill = self._clock()
            return

        now = self._clock()
        elapsed = max(0.0, now - self._last_refill)
        self._tokens = min(self.capacity_bits, self._tokens + elapsed * self.rate_bps)
        self._last_refill = now


def calculate_delay_seconds(
    latency_ms: int,
    jitter_ms: int = 0,
    *,
    uniform: Callable[[float, float], float] = random.uniform,
) -> float:
    """Return latency plus random jitter as seconds."""

    if latency_ms < 0:
        raise ValueError("latency_ms must be non-negative")
    if jitter_ms < 0:
        raise ValueError("jitter_ms must be non-negative")

    delay_ms = float(latency_ms)
    if jitter_ms:
        delay_ms += uniform(-float(jitter_ms), float(jitter_ms))
    return max(0.0, delay_ms / 1000)


def should_drop_chunk(
    loss_pct: float,
    *,
    random_value: Callable[[], float] = random.random,
) -> bool:
    """Return whether a chunk should be dropped for the configured loss rate."""

    if loss_pct < 0 or loss_pct > 1:
        raise ValueError("loss_pct must be between 0.0 and 1.0")
    if loss_pct == 0:
        return False
    if loss_pct == 1:
        return True
    return random_value() < loss_pct
