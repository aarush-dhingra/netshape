"""Parsing helpers for user-facing network shaping values."""

from __future__ import annotations

import re


class UnitParseError(ValueError):
    """Raised when a user-provided unit value cannot be parsed."""


_VALUE_RE = re.compile(r"^\s*(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z/%]+)\s*$")

_BANDWIDTH_UNITS = {
    "bps": 1,
    "bit/s": 1,
    "bits/s": 1,
    "kbps": 1_000,
    "kbit/s": 1_000,
    "kbits/s": 1_000,
    "mbps": 1_000_000,
    "mbit/s": 1_000_000,
    "mbits/s": 1_000_000,
    "gbps": 1_000_000_000,
    "gbit/s": 1_000_000_000,
    "gbits/s": 1_000_000_000,
    "b/s": 8,
    "byte/s": 8,
    "bytes/s": 8,
    "kb/s": 8_000,
    "kbyte/s": 8_000,
    "kbytes/s": 8_000,
    "mb/s": 8_000_000,
    "mbyte/s": 8_000_000,
    "mbytes/s": 8_000_000,
    "gb/s": 8_000_000_000,
    "gbyte/s": 8_000_000_000,
    "gbytes/s": 8_000_000_000,
}

_DURATION_UNITS_MS = {
    "ms": 1,
    "millisecond": 1,
    "milliseconds": 1,
    "s": 1_000,
    "sec": 1_000,
    "second": 1_000,
    "seconds": 1_000,
}


def _parse_number_and_unit(raw: str, *, kind: str) -> tuple[float, str]:
    if not raw or not raw.strip():
        raise UnitParseError(f"{kind} must not be empty")

    match = _VALUE_RE.match(raw)
    if not match:
        raise UnitParseError(f"invalid {kind}: {raw!r}")

    value = float(match.group("number"))
    if value < 0:
        raise UnitParseError(f"{kind} must be non-negative")

    return value, match.group("unit").lower()


def parse_bandwidth(raw: str | int | float | None) -> int:
    """Parse a bandwidth value into bits per second.

    Numeric values are treated as already being bits per second. Strings must
    include an explicit unit such as ``100kbps`` or ``50KB/s``.
    """

    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        if raw < 0:
            raise UnitParseError("bandwidth must be non-negative")
        return int(raw)

    value, unit = _parse_number_and_unit(raw, kind="bandwidth")
    multiplier = _BANDWIDTH_UNITS.get(unit)
    if multiplier is None:
        raise UnitParseError(f"unsupported bandwidth unit: {unit!r}")
    return int(value * multiplier)


def parse_duration_ms(raw: str | int | float | None, *, kind: str = "duration") -> int:
    """Parse a duration value into milliseconds."""

    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        if raw < 0:
            raise UnitParseError(f"{kind} must be non-negative")
        return int(raw)

    value, unit = _parse_number_and_unit(raw, kind=kind)
    multiplier = _DURATION_UNITS_MS.get(unit)
    if multiplier is None:
        raise UnitParseError(f"unsupported {kind} unit: {unit!r}")
    return int(value * multiplier)


def parse_latency(raw: str | int | float | None) -> int:
    """Parse latency into milliseconds."""

    return parse_duration_ms(raw, kind="latency")


def parse_jitter(raw: str | int | float | None) -> int:
    """Parse jitter into milliseconds."""

    return parse_duration_ms(raw, kind="jitter")


def parse_loss(raw: str | int | float | None) -> float:
    """Parse packet loss into a 0.0-1.0 fraction."""

    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        if raw < 0:
            raise UnitParseError("loss must be non-negative")
        value = raw / 100 if raw > 1 else raw
    else:
        value, unit = _parse_number_and_unit(raw, kind="loss")
        if unit not in {"%", "percent", "pct"}:
            raise UnitParseError(f"unsupported loss unit: {unit!r}")
        value = value / 100

    if value > 1:
        raise UnitParseError("loss must be between 0% and 100%")
    return float(value)
