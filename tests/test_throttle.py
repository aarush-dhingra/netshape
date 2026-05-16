from __future__ import annotations

import math

import pytest

from netshape.throttle import TokenBucket, calculate_delay_seconds, should_drop_chunk


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
    bucket = TokenBucket(8_000, clock=clock)

    assert bucket.consume(500) == 0.0
    assert bucket.tokens == 4_000


def test_token_bucket_returns_wait_for_shortage() -> None:
    clock = ManualClock()
    bucket = TokenBucket(8_000, clock=clock)

    assert bucket.consume(2_000) == pytest.approx(1.0)
    assert bucket.tokens == 0

    clock.advance(0.5)
    assert bucket.consume(500) == 0.0
    assert bucket.tokens == 0


def test_token_bucket_refills_up_to_capacity() -> None:
    clock = ManualClock()
    bucket = TokenBucket(8_000, clock=clock)

    bucket.consume(1_000)
    clock.advance(10)

    assert bucket.tokens == 8_000


def test_token_bucket_can_reset_rate() -> None:
    clock = ManualClock()
    bucket = TokenBucket(8_000, clock=clock)

    bucket.consume(1_000)
    bucket.reset_rate(16_000)

    assert bucket.rate_bps == 16_000
    assert bucket.capacity_bits == 16_000
    assert bucket.consume(2_000) == pytest.approx(1.0)


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
