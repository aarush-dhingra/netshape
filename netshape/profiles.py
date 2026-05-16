"""Profile loading, saving, validation, and resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from netshape.units import parse_bandwidth, parse_jitter, parse_latency, parse_loss


class ProfileError(Exception):
    """Raised when a profile cannot be found or is invalid."""


@dataclass(frozen=True)
class ResolvedProfile:
    """A profile with all values parsed to their canonical numeric forms."""

    name: str | None
    description: str
    bandwidth_bps: int
    latency_ms: int
    loss_pct: float
    jitter_ms: int


_BUILTIN_CACHE: dict[str, dict[str, str]] | None = None
_REQUIRED_FIELDS = {"bandwidth", "latency", "loss", "jitter"}


def _load_builtin_raw() -> dict[str, dict[str, str]]:
    global _BUILTIN_CACHE
    if _BUILTIN_CACHE is not None:
        return _BUILTIN_CACHE

    data_files = resources.files("netshape.data")
    profile_file = data_files.joinpath("default_profiles.json")
    raw = profile_file.read_text(encoding="utf-8")
    _BUILTIN_CACHE = json.loads(raw)
    return _BUILTIN_CACHE  # type: ignore[return-value]


def _netshape_dir() -> Path:
    return Path.home() / ".netshape"


def _load_custom_profiles(directory: Path) -> dict[str, dict[str, str]]:
    """Load all .json profiles from a directory."""
    profiles: dict[str, dict[str, str]] = {}
    if not directory.exists():
        return profiles
    for path in directory.glob("*.json"):
        try:
            with open(path) as f:
                data = json.load(f)
            if _REQUIRED_FIELDS.issubset(data.keys()):
                profiles[path.stem] = data
        except (json.JSONDecodeError, OSError):
            continue
    return profiles


def _parse_profile(name: str | None, raw: dict[str, Any]) -> ResolvedProfile:
    """Parse a raw profile dict into a ResolvedProfile."""
    return ResolvedProfile(
        name=name,
        description=raw.get("description", ""),
        bandwidth_bps=parse_bandwidth(raw["bandwidth"]),
        latency_ms=parse_latency(raw["latency"]),
        loss_pct=parse_loss(raw["loss"]),
        jitter_ms=parse_jitter(raw["jitter"]),
    )


def list_builtin() -> dict[str, dict[str, str]]:
    """Return all built-in profiles as raw dicts."""
    return dict(_load_builtin_raw())


def list_custom() -> dict[str, dict[str, str]]:
    """Return user-saved custom profiles from ~/.netshape/profiles/."""
    return _load_custom_profiles(_netshape_dir() / "profiles")


def list_project() -> dict[str, dict[str, str]]:
    """Return project-level profiles from ./.netshape/profiles/."""
    return _load_custom_profiles(Path.cwd() / ".netshape" / "profiles")


def list_all() -> dict[str, dict[str, str]]:
    """Return all profiles, merged. Priority: built-in < global custom < project-local."""
    merged = {}
    merged.update(list_builtin())
    merged.update(list_custom())
    merged.update(list_project())
    return merged


def resolve_profile(
    profile_name: str | None = None,
    bandwidth: str | None = None,
    latency: str | None = None,
    loss: str | None = None,
    jitter: str | None = None,
) -> ResolvedProfile:
    """Resolve a profile name and/or custom overrides into a ResolvedProfile.

    If profile_name is given, load it and optionally override individual values.
    If no profile_name, all four values must be provided.
    """
    if profile_name:
        all_profiles = list_all()
        if profile_name not in all_profiles:
            available = ", ".join(sorted(all_profiles.keys()))
            raise ProfileError(
                f"Unknown profile: '{profile_name}'. Available: {available}"
            )
        raw = dict(all_profiles[profile_name])

        if bandwidth is not None:
            raw["bandwidth"] = bandwidth
        if latency is not None:
            raw["latency"] = latency
        if loss is not None:
            raw["loss"] = loss
        if jitter is not None:
            raw["jitter"] = jitter

        return _parse_profile(profile_name, raw)

    # No profile name — need at minimum bandwidth
    if bandwidth is None:
        raise ProfileError(
            "Provide either --profile or at least --bandwidth."
        )

    raw = {
        "bandwidth": bandwidth,
        "latency": latency or "0ms",
        "loss": loss or "0%",
        "jitter": jitter or "0ms",
    }
    return _parse_profile(None, raw)


def save_custom(
    name: str,
    bandwidth: str,
    latency: str,
    loss: str,
    jitter: str,
    description: str = "",
    base_dir: Path | None = None,
) -> Path:
    """Save a custom profile to ~/.netshape/profiles/."""
    import re
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", name):
        raise ProfileError(
            f"Invalid profile name: '{name}'. Use alphanumeric, hyphens, and underscores only."
        )

    builtins = _load_builtin_raw()
    if name in builtins:
        raise ProfileError(f"Cannot overwrite built-in profile: '{name}'.")

    # Validate values parse correctly
    parse_bandwidth(bandwidth)
    parse_latency(latency)
    parse_loss(loss)
    parse_jitter(jitter)

    profile_dir = (base_dir or _netshape_dir()) / "profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "name": name,
        "bandwidth": bandwidth,
        "latency": latency,
        "loss": loss,
        "jitter": jitter,
        "description": description,
    }

    path = profile_dir / f"{name}.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    return path


def delete_custom(name: str, base_dir: Path | None = None) -> None:
    """Delete a custom profile. Refuses to delete built-ins."""
    builtins = _load_builtin_raw()
    if name in builtins:
        raise ProfileError(f"Cannot delete built-in profile: '{name}'.")

    profile_path = (base_dir or _netshape_dir()) / "profiles" / f"{name}.json"
    if not profile_path.exists():
        raise ProfileError(f"Custom profile not found: '{name}'.")
    profile_path.unlink()
