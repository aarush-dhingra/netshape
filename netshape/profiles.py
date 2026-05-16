"""Built-in network profile loading and resolution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

from .units import parse_bandwidth, parse_jitter, parse_latency, parse_loss


class ProfileError(ValueError):
    """Raised when a profile cannot be loaded or resolved."""


PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class NetworkProfile:
    name: str
    description: str
    bandwidth_bps: int
    latency_ms: int
    loss_pct: float
    jitter_ms: int


@dataclass(frozen=True)
class ThrottleSettings:
    bandwidth_bps: int = 0
    latency_ms: int = 0
    loss_pct: float = 0.0
    jitter_ms: int = 0
    profile: str | None = None


def validate_profile_name(name: str) -> str:
    if not PROFILE_NAME_RE.match(name):
        raise ProfileError(
            "profile names must start with a lowercase letter or number and "
            "contain only lowercase letters, numbers, underscores, or hyphens"
        )
    return name


def load_builtin_profiles() -> dict[str, NetworkProfile]:
    profile_path = resources.files("netshape.data").joinpath("default_profiles.json")
    raw_profiles = json.loads(profile_path.read_text(encoding="utf-8"))

    profiles: dict[str, NetworkProfile] = {}
    for name, raw_profile in raw_profiles.items():
        validate_profile_name(name)
        profiles[name] = _profile_from_mapping(name, raw_profile)
    return profiles


def list_builtin_profiles() -> list[NetworkProfile]:
    return sorted(load_builtin_profiles().values(), key=lambda profile: profile.name)


def get_builtin_profile(name: str) -> NetworkProfile:
    validate_profile_name(name)
    profiles = load_builtin_profiles()
    try:
        return profiles[name]
    except KeyError as exc:
        raise ProfileError(f"unknown profile: {name}") from exc


def resolve_settings(
    *,
    profile: str | None = None,
    bandwidth: str | int | float | None = None,
    latency: str | int | float | None = None,
    loss: str | int | float | None = None,
    jitter: str | int | float | None = None,
) -> ThrottleSettings:
    """Resolve profile defaults plus explicit CLI overrides."""

    if profile is None:
        settings = ThrottleSettings()
    else:
        builtin = get_builtin_profile(profile)
        settings = ThrottleSettings(
            bandwidth_bps=builtin.bandwidth_bps,
            latency_ms=builtin.latency_ms,
            loss_pct=builtin.loss_pct,
            jitter_ms=builtin.jitter_ms,
            profile=builtin.name,
        )

    return ThrottleSettings(
        bandwidth_bps=settings.bandwidth_bps if bandwidth is None else parse_bandwidth(bandwidth),
        latency_ms=settings.latency_ms if latency is None else parse_latency(latency),
        loss_pct=settings.loss_pct if loss is None else parse_loss(loss),
        jitter_ms=settings.jitter_ms if jitter is None else parse_jitter(jitter),
        profile=settings.profile,
    )


def _profile_from_mapping(name: str, raw_profile: dict[str, Any]) -> NetworkProfile:
    try:
        description = str(raw_profile["description"])
        bandwidth_bps = int(raw_profile["bandwidth_bps"])
        latency_ms = int(raw_profile["latency_ms"])
        loss_pct = float(raw_profile["loss_pct"])
        jitter_ms = int(raw_profile["jitter_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProfileError(f"invalid profile definition: {name}") from exc

    if bandwidth_bps < 0:
        raise ProfileError(f"profile {name!r} has negative bandwidth")
    if latency_ms < 0:
        raise ProfileError(f"profile {name!r} has negative latency")
    if loss_pct < 0 or loss_pct > 1:
        raise ProfileError(f"profile {name!r} has loss outside 0.0-1.0")
    if jitter_ms < 0:
        raise ProfileError(f"profile {name!r} has negative jitter")

    return NetworkProfile(
        name=name,
        description=description,
        bandwidth_bps=bandwidth_bps,
        latency_ms=latency_ms,
        loss_pct=loss_pct,
        jitter_ms=jitter_ms,
    )
