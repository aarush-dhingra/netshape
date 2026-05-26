from __future__ import annotations

import math

import pytest

from netshape.throttle import (
    TokenBucket,
    _MAX_BURST_BITS,
    _MIN_BURST_BITS,
    _default_capacity,
    calculate_delay_seconds,
    should_drop_chunk,
)


class ManualClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_unlimited_token_bucket_never_waits() -> None:
    bucket = TokenBucket(0)

    assert bucket.consume(10_000_000) == 0.0
    assert math.isinf(bucket.tokens)


def test_token_bucket_consumes_available_capacity() -> None:
    clock = ManualClock()
    # Explicit capacity so the test is independent of _default_capacity.
    bucket = TokenBucket(8_000, capacity_bits=8_000, clock=clock)

    assert bucket.consume(500) == 0.0
    assert bucket.tokens == 4_000


def test_token_bucket_returns_wait_for_shortage() -> None:
    clock = ManualClock()
    bucket = TokenBucket(8_000, capacity_bits=8_000, clock=clock)

    assert bucket.consume(2_000) == pytest.approx(1.0)
    assert bucket.tokens == 0

    clock.advance(0.5)
    assert bucket.consume(500) == 0.0
    assert bucket.tokens == 0


def test_token_bucket_refills_up_to_capacity() -> None:
    clock = ManualClock()
    bucket = TokenBucket(8_000, capacity_bits=8_000, clock=clock)

    bucket.consume(1_000)
    clock.advance(10)

    assert bucket.tokens == 8_000


def test_token_bucket_can_reset_rate() -> None:
    clock = ManualClock()
    # Start with explicit capacity so the bucket is fully drained by consume.
    bucket = TokenBucket(8_000, capacity_bits=8_000, clock=clock)

    bucket.consume(1_000)  # drains all 8 000 bits; tokens = 0
    bucket.reset_rate(16_000)

    assert bucket.rate_bps == 16_000
    assert bucket.capacity_bits == _default_capacity(16_000)
    # tokens = 0 after reset (min(0, new_capacity)); shortage = 16 000 bits at 16 000 bps → 1 s
    assert bucket.consume(2_000) == pytest.approx(1.0)


def test_default_capacity_proportional() -> None:
    """_default_capacity scales with rate and stays within [MIN, MAX]."""
    # Below crossover (~655 Kbps): floor kicks in
    assert _default_capacity(0) == 0
    assert _default_capacity(100_000) == _MIN_BURST_BITS
    assert _default_capacity(250_000) == _MIN_BURST_BITS
    # Above crossover but below cap: proportional
    one_mbps_cap = _default_capacity(1_000_000)
    assert _MIN_BURST_BITS < one_mbps_cap < _MAX_BURST_BITS
    assert one_mbps_cap == int(1_000_000 * 0.1)
    # Above cap (~1.3 Mbps+): cap kicks in
    assert _default_capacity(6_000_000) == _MAX_BURST_BITS
    assert _default_capacity(100_000_000) == _MAX_BURST_BITS


def test_calculate_delay_without_jitter() -> None:
    assert calculate_delay_seconds(250) == pytest.approx(0.25)


def test_calculate_delay_with_jitter() -> None:
    delay = calculate_delay_seconds(250, 50, uniform=lambda lower, upper: upper)

    assert delay == pytest.approx(0.3)


def test_calculate_delay_never_goes_negative() -> None:
    delay = calculate_delay_seconds(10, 50, uniform=lambda lower, upper: lower)

    assert delay == 0


@pytest.mark.parametrize(
    ("loss_pct", "random_value", "expected"),
    [
        (0.0, 0.0, False),
        (1.0, 0.999, True),
        (0.25, 0.1, True),
        (0.25, 0.9, False),
    ],
)
def test_should_drop_chunk(loss_pct: float, random_value: float, expected: bool) -> None:
    assert should_drop_chunk(loss_pct, random_value=lambda: random_value) is expected


@pytest.mark.parametrize("rate_bps", [-1])
def test_token_bucket_rejects_negative_rates(rate_bps: int) -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate_bps)


@pytest.mark.parametrize(
    ("latency_ms", "jitter_ms"),
    [(-1, 0), (0, -1)],
)
def test_delay_rejects_negative_values(latency_ms: int, jitter_ms: int) -> None:
    with pytest.raises(ValueError):
        calculate_delay_seconds(latency_ms, jitter_ms)


@pytest.mark.parametrize("loss_pct", [-0.1, 1.1])
def test_should_drop_chunk_rejects_invalid_loss(loss_pct: float) -> None:
    with pytest.raises(ValueError):
        should_drop_chunk(loss_pct)
