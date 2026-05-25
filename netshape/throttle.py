"""Standalone throttling primitives used by the proxy."""

from __future__ import annotations

import random
import time
from collections.abc import Callable

# Burst window constants.
#
# The burst capacity scales proportionally with the rate (10 % of rate_bps) so that
# any configured rate gets roughly a 100 ms burst window before the token bucket
# starts throttling.
#
# Floor (_MIN_BURST_BITS = one read-chunk = 65 536 bits):
#   Mandatory for correctness.  The bucket capacity must be at least as large as one
#   READ_CHUNK_SIZE chunk; otherwise the bucket can never accumulate enough tokens to
#   cover a full chunk, and the effective throughput diverges from the configured rate.
#
# Cap (_MAX_BURST_BITS = two read-chunks = 131 072 bits):
#   Keeps high-speed connections tightly throttled (22 ms burst at 6 Mbps, 1.3 ms
#   at 100 Mbps) without allowing the bucket to become a free-pass for large transfers.
#
# Crossover points:
#   rate × 0.1 < 65 536  (i.e. rate < ~655 Kbps) → floor wins → 1-chunk burst
#   rate × 0.1 > 131 072 (i.e. rate > ~1.3 Mbps)  → cap wins  → 2-chunk burst
#   between:                                          proportional (65 536–131 072 bits)
_BURST_RATIO: float = 0.1              # 100 ms proportional window
_MIN_BURST_BITS: int = 8192 * 8        # 65 536 bits — one read-chunk (mandatory floor)
_MAX_BURST_BITS: int = 2 * 8192 * 8    # 131 072 bits — two read-chunks (cap)


def _default_capacity(rate_bps: int) -> int:
    """Return the default burst capacity for a given rate.

    Scales as 10 % of ``rate_bps``, clamped to [``_MIN_BURST_BITS``,
    ``_MAX_BURST_BITS``].
    """
    if rate_bps == 0:
        return 0
    proportional = int(rate_bps * _BURST_RATIO)
    return max(_MIN_BURST_BITS, min(proportional, _MAX_BURST_BITS))


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
        self.capacity_bits = int(
            capacity_bits if capacity_bits is not None else _default_capacity(rate_bps)
        )
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
        self.capacity_bits = int(_default_capacity(rate_bps))
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
    """Return whether an incoming connection should be dropped for the configured loss rate.

    Loss is simulated at the **connection level**: each new connection is randomly
    rejected before any data is exchanged.  This avoids corrupting TLS streams,
    which would happen if individual encrypted chunks were silently discarded
    mid-stream (the remote TLS peer would receive a broken record and abort with
    a decryption error).  Connection-level drop is statistically equivalent to
    packet loss for the application layer — the connection simply never succeeds.
    """

    if loss_pct < 0 or loss_pct > 1:
        raise ValueError("loss_pct must be between 0.0 and 1.0")
    if loss_pct == 0:
        return False
    if loss_pct == 1:
        return True
    return random_value() < loss_pct
