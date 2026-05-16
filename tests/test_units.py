from __future__ import annotations

import pytest

from netshape.units import (
    UnitParseError,
    parse_bandwidth,
    parse_jitter,
    parse_latency,
    parse_loss,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 0),
        (50000, 50000),
        ("100bps", 100),
        ("250kbps", 250_000),
        ("1.5mbps", 1_500_000),
        ("2 gbps", 2_000_000_000),
        ("50KB/s", 400_000),
        ("1MB/s", 8_000_000),
    ],
)
def test_parse_bandwidth(raw: str | int | None, expected: int) -> None:
    assert parse_bandwidth(raw) == expected


@pytest.mark.parametrize("raw", ["", "fast", "100", "10mph", "-1kbps"])
def test_parse_bandwidth_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(UnitParseError):
        parse_bandwidth(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 0),
        (250, 250),
        ("100ms", 100),
        ("1.5s", 1500),
        ("2 seconds", 2000),
    ],
)
def test_parse_latency(raw: str | int | None, expected: int) -> None:
    assert parse_latency(raw) == expected


def test_parse_jitter_uses_duration_units() -> None:
    assert parse_jitter("750ms") == 750
    assert parse_jitter("1s") == 1000


@pytest.mark.parametrize("parser", [parse_latency, parse_jitter])
@pytest.mark.parametrize("raw", ["", "slow", "10", "2minutes", "-1ms"])
def test_duration_parsers_reject_invalid_values(parser, raw: str) -> None:
    with pytest.raises(UnitParseError):
        parser(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 0.0),
        (0, 0.0),
        (0.25, 0.25),
        (5, 0.05),
        ("1%", 0.01),
        ("2.5 percent", 0.025),
        ("10pct", 0.10),
    ],
)
def test_parse_loss(raw: str | int | float | None, expected: float) -> None:
    assert parse_loss(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "some", "1", "101%", "-1%"])
def test_parse_loss_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(UnitParseError):
        parse_loss(raw)
