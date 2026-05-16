"""Parse and validate network parameter values (bandwidth, latency, loss, jitter)."""

from __future__ import annotations

import re


class UnitParseError(ValueError):
    """Raised when a value string cannot be parsed into a valid network parameter."""


_BANDWIDTH_PATTERN = re.compile(
    r"^(?P<number>[0-9]*\.?[0-9]+)\s*(?P<unit>[a-zA-Z/]*)\s*$"
)

_LATENCY_PATTERN = re.compile(
    r"^(?P<number>[0-9]*\.?[0-9]+)\s*(?P<unit>[a-zA-Z]*)\s*$"
)

_LOSS_PATTERN = re.compile(
    r"^(?P<number>[0-9]*\.?[0-9]+)\s*(?P<unit>%?)\s*$"
)


def parse_bandwidth(value: str) -> int:
    """Parse a bandwidth string and return bits per second.

    Accepted formats:
        500kbps, 500kb  -> 500_000 bps
        0.5mbps, 0.5mb  -> 500_000 bps
        62.5KB/s         -> 500_000 bps (bytes, multiplied by 8)
        62500bps         -> 62_500 bps
        500              -> raises with hint (ambiguous)
    """
    value = value.strip()
    match = _BANDWIDTH_PATTERN.match(value)
    if not match:
        raise UnitParseError(f"Cannot parse bandwidth value: '{value}'")

    number = float(match.group("number"))
    raw_unit = match.group("unit")

    # Detect byte-based units (capital B) before lowering
    is_bytes = "B" in raw_unit and "b" not in raw_unit

    # Normalize: strip trailing /s, lowercase
    unit = raw_unit.lower()
    if unit.endswith("/s"):
        unit = unit[:-2]

    if not unit:
        raise UnitParseError(
            f"Ambiguous bandwidth value: '{value}'. "
            f"Did you mean {value}kbps or {value}mbps?\n"
            f"  Use: --bandwidth {value}kbps  or  --bandwidth {value}mbps"
        )

    bit_multipliers: dict[str, float] = {
        "bps": 1,
        "kbps": 1_000,
        "kb": 1_000,
        "kbit": 1_000,
        "mbps": 1_000_000,
        "mb": 1_000_000,
        "mbit": 1_000_000,
        "gbps": 1_000_000_000,
        "gb": 1_000_000_000,
        "gbit": 1_000_000_000,
    }

    byte_multipliers: dict[str, float] = {
        "b": 8,
        "kb": 8_000,
        "mb": 8_000_000,
        "gb": 8_000_000_000,
    }

    unit_multipliers = byte_multipliers if is_bytes else bit_multipliers

    if unit not in unit_multipliers:
        raise UnitParseError(
            f"Unknown bandwidth unit: '{match.group('unit')}'. "
            f"Supported: kbps, mbps, gbps, KB/s, MB/s, bps"
        )

    bps = number * unit_multipliers[unit]
    return int(round(bps))


def parse_latency(value: str) -> int:
    """Parse a latency string and return milliseconds.

    Accepted formats:
        200ms   -> 200
        0.2s    -> 200
        200     -> 200 (bare number assumes ms)
    """
    value = value.strip()
    match = _LATENCY_PATTERN.match(value)
    if not match:
        raise UnitParseError(f"Cannot parse latency value: '{value}'")

    number = float(match.group("number"))
    unit = match.group("unit").lower()

    if unit in ("", "ms"):
        return int(round(number))
    elif unit == "s":
        return int(round(number * 1000))
    else:
        raise UnitParseError(
            f"Unknown latency unit: '{unit}'. Supported: ms, s"
        )


def parse_loss(value: str) -> float:
    """Parse a packet loss string and return a fraction (0.0 to 1.0).

    Accepted formats:
        2%    -> 0.02
        0.02  -> 0.02 (bare number treated as fraction)
        100%  -> 1.0
    """
    value = value.strip()
    match = _LOSS_PATTERN.match(value)
    if not match:
        raise UnitParseError(f"Cannot parse packet loss value: '{value}'")

    number = float(match.group("number"))
    unit = match.group("unit")

    if unit == "%":
        return number / 100.0
    else:
        if number > 1.0:
            raise UnitParseError(
                f"Ambiguous loss value: '{value}'. "
                f"Values over 1.0 without '%' are invalid. Did you mean {value}%?"
            )
        return number


def parse_jitter(value: str) -> int:
    """Parse a jitter string and return milliseconds. Same format as latency."""
    return parse_latency(value)


def validate_ranges(
    bandwidth_bps: int,
    latency_ms: int,
    loss_pct: float,
    jitter_ms: int,
) -> None:
    """Validate that all parsed network parameters are within acceptable ranges."""
    errors: list[str] = []

    if bandwidth_bps < 0:
        errors.append(f"Bandwidth cannot be negative (got {bandwidth_bps} bps).")

    if latency_ms < 0:
        errors.append(f"Latency cannot be negative (got {latency_ms} ms).")

    if not (0.0 <= loss_pct <= 1.0):
        errors.append(
            f"Packet loss must be between 0% and 100% (got {loss_pct * 100:.1f}%)."
        )

    if jitter_ms < 0:
        errors.append(f"Jitter cannot be negative (got {jitter_ms} ms).")

    if jitter_ms > latency_ms and latency_ms > 0:
        errors.append(
            f"Jitter ({jitter_ms} ms) cannot exceed latency ({latency_ms} ms)."
        )

    if errors:
        raise UnitParseError("\n".join(errors))
