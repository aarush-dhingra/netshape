"""Tests for netshape.units — bandwidth, latency, loss, jitter parsing."""

from __future__ import annotations

import pytest

from netshape.units import (
    UnitParseError,
    parse_bandwidth,
    parse_jitter,
    parse_latency,
    parse_loss,
    validate_ranges,
)


class TestParseBandwidth:
    @pytest.mark.parametrize(
        "value, expected_bps",
        [
            ("500kbps", 500_000),
            ("500kb", 500_000),
            ("500kbit", 500_000),
            ("0.5mbps", 500_000),
            ("0.5mb", 500_000),
            ("1mbps", 1_000_000),
            ("1.5mbps", 1_500_000),
            ("62500bps", 62_500),
            ("1gbps", 1_000_000_000),
            ("0.001gbps", 1_000_000),
            # Byte-based (capital B in original, lowered internally)
            ("62.5KB/s", 500_000),
            ("1MB/s", 8_000_000),
        ],
    )
    def test_valid_values(self, value: str, expected_bps: int) -> None:
        assert parse_bandwidth(value) == expected_bps

    def test_bare_number_raises_ambiguous(self) -> None:
        with pytest.raises(UnitParseError, match="Ambiguous"):
            parse_bandwidth("500")

    def test_unknown_unit_raises(self) -> None:
        with pytest.raises(UnitParseError, match="Unknown bandwidth unit"):
            parse_bandwidth("500xyz")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(UnitParseError):
            parse_bandwidth("")

    def test_negative_looking_string_raises(self) -> None:
        with pytest.raises(UnitParseError):
            parse_bandwidth("-500kbps")

    def test_whitespace_is_trimmed(self) -> None:
        assert parse_bandwidth("  500kbps  ") == 500_000


class TestParseLatency:
    @pytest.mark.parametrize(
        "value, expected_ms",
        [
            ("200ms", 200),
            ("200", 200),
            ("0.2s", 200),
            ("1s", 1000),
            ("1.5s", 1500),
            ("0ms", 0),
            ("50", 50),
        ],
    )
    def test_valid_values(self, value: str, expected_ms: int) -> None:
        assert parse_latency(value) == expected_ms

    def test_unknown_unit_raises(self) -> None:
        with pytest.raises(UnitParseError, match="Unknown latency unit"):
            parse_latency("200min")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(UnitParseError):
            parse_latency("")


class TestParseLoss:
    @pytest.mark.parametrize(
        "value, expected_fraction",
        [
            ("2%", 0.02),
            ("0.02", 0.02),
            ("100%", 1.0),
            ("0%", 0.0),
            ("0.0", 0.0),
            ("1.0", 1.0),
            ("0.5", 0.5),
            ("10.5%", 0.105),
        ],
    )
    def test_valid_values(self, value: str, expected_fraction: float) -> None:
        result = parse_loss(value)
        assert abs(result - expected_fraction) < 1e-9

    def test_bare_number_over_one_raises(self) -> None:
        with pytest.raises(UnitParseError, match="Ambiguous"):
            parse_loss("5")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(UnitParseError):
            parse_loss("")


class TestParseJitter:
    def test_parses_like_latency(self) -> None:
        assert parse_jitter("30ms") == 30
        assert parse_jitter("0.03s") == 30
        assert parse_jitter("30") == 30


class TestValidateRanges:
    def test_valid_ranges_pass(self) -> None:
        validate_ranges(500_000, 200, 0.01, 20)

    def test_negative_bandwidth_raises(self) -> None:
        with pytest.raises(UnitParseError, match="Bandwidth cannot be negative"):
            validate_ranges(-1, 200, 0.01, 20)

    def test_negative_latency_raises(self) -> None:
        with pytest.raises(UnitParseError, match="Latency cannot be negative"):
            validate_ranges(500_000, -1, 0.01, 0)

    def test_loss_over_100_raises(self) -> None:
        with pytest.raises(UnitParseError, match="Packet loss must be between"):
            validate_ranges(500_000, 200, 1.5, 20)

    def test_loss_negative_raises(self) -> None:
        with pytest.raises(UnitParseError, match="Packet loss must be between"):
            validate_ranges(500_000, 200, -0.1, 20)

    def test_negative_jitter_raises(self) -> None:
        with pytest.raises(UnitParseError, match="Jitter cannot be negative"):
            validate_ranges(500_000, 200, 0.01, -1)

    def test_jitter_exceeds_latency_raises(self) -> None:
        with pytest.raises(UnitParseError, match="Jitter.*cannot exceed latency"):
            validate_ranges(500_000, 50, 0.01, 100)

    def test_zero_latency_with_jitter_is_ok(self) -> None:
        validate_ranges(500_000, 0, 0.01, 30)

    def test_zero_everything_passes(self) -> None:
        validate_ranges(0, 0, 0.0, 0)
