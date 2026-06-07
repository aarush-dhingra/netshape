"""YAML scenario scripting engine for NetShape."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .profiles import ProfileError, get_builtin_profile
from .units import parse_bandwidth, parse_duration_ms, parse_jitter, parse_latency, parse_loss

USER_SCENARIOS_DIR = Path.home() / ".netshape" / "scenarios"


class ScenarioError(ValueError):
    """Raised when a scenario file is invalid or cannot be executed."""


@dataclass(frozen=True)
class Phase:
    """A single phase in a scenario with an explicit duration and throttle config."""

    name: str
    duration_ms: int
    bandwidth_bps: int
    latency_ms: int
    loss_pct: float
    jitter_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_config(self) -> dict[str, Any]:
        """Return a dict suitable for passing to ThrottledProxy.configure()."""
        return {
            "bandwidth_bps": self.bandwidth_bps,
            "latency_ms": self.latency_ms,
            "loss_pct": self.loss_pct,
            "jitter_ms": self.jitter_ms,
        }


@dataclass(frozen=True)
class Scenario:
    """An ordered sequence of network conditions over time."""

    name: str
    description: str
    phases: tuple[Phase, ...]

    def total_duration_ms(self) -> int:
        return sum(p.duration_ms for p in self.phases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "phases": [p.to_dict() for p in self.phases],
        }


# ── Public loaders ────────────────────────────────────────────────────────────

def load_scenario(path: Path | str) -> Scenario:
    """Load and parse a scenario from a YAML file. Requires pyyaml."""
    _require_yaml()
    import yaml  # type: ignore[import]

    path = Path(path)
    if not path.exists():
        raise ScenarioError(f"scenario file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ScenarioError(f"failed to parse YAML {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ScenarioError("scenario file must contain a YAML mapping")
    return parse_scenario_dict(data)


def load_builtin_scenario(name: str) -> Scenario:
    """Load a built-in scenario by name (without .yaml extension). Requires pyyaml."""
    import importlib.resources

    _require_yaml()
    import yaml  # type: ignore[import]

    try:
        content = (
            importlib.resources.files("netshape.data.scenarios")
            .joinpath(f"{name}.yaml")
            .read_text("utf-8")
        )
    except FileNotFoundError as exc:
        raise ScenarioError(f"built-in scenario not found: {name!r}") from exc
    try:
        data = yaml.safe_load(content)
    except Exception as exc:
        raise ScenarioError(f"failed to parse built-in scenario {name!r}: {exc}") from exc
    return parse_scenario_dict(data)


def list_builtin_scenarios() -> list[str]:
    """Return names of available built-in scenarios (no .yaml extension)."""
    import importlib.resources

    try:
        pkg = importlib.resources.files("netshape.data.scenarios")
        names: list[str] = []
        for item in pkg.iterdir():
            raw = str(item)
            for sep in ("/", "\\"):
                raw = raw.rsplit(sep, 1)[-1]
            if raw.endswith(".yaml"):
                names.append(raw[:-5])
        return sorted(names)
    except Exception:
        return []


def list_user_scenarios() -> list[str]:
    """Return names of user-saved scenarios from ~/.netshape/scenarios/."""
    if not USER_SCENARIOS_DIR.exists():
        return []
    return sorted(p.stem for p in USER_SCENARIOS_DIR.glob("*.json"))


def save_user_scenario(scenario_dict: dict[str, Any]) -> Path:
    """Save a scenario dict to ~/.netshape/scenarios/<name>.json. Returns the path."""
    import re

    name = str(scenario_dict.get("name", "scenario")).strip()
    if not name:
        raise ScenarioError("scenario name is required")
    safe = re.sub(r"[^\w\-]", "_", name)
    if not safe:
        raise ScenarioError("scenario name produced an empty filename after sanitization")
    USER_SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    dest = (USER_SCENARIOS_DIR / f"{safe}.json").resolve()
    # Guard against the unlikely edge case where sanitization still yields a traversal.
    try:
        dest.relative_to(USER_SCENARIOS_DIR.resolve())
    except ValueError:
        raise ScenarioError(f"invalid scenario name: {name!r}")
    dest.write_text(json.dumps(scenario_dict, indent=2), encoding="utf-8")
    return dest


def load_user_scenario(name: str) -> Scenario:
    """Load a user-saved scenario by name from ~/.netshape/scenarios/."""
    path = (USER_SCENARIOS_DIR / f"{name}.json").resolve()
    # Prevent path traversal: the resolved path must stay inside USER_SCENARIOS_DIR.
    try:
        path.relative_to(USER_SCENARIOS_DIR.resolve())
    except ValueError:
        raise ScenarioError(f"invalid scenario name: {name!r}")
    if not path.exists():
        raise ScenarioError(f"user scenario not found: {name!r}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ScenarioError(f"failed to read user scenario {name!r}: {exc}") from exc
    return parse_scenario_dict(data)


def parse_scenario_dict(data: dict[str, Any]) -> Scenario:
    """Parse a scenario from a pre-loaded dict (e.g. already parsed from YAML or JSON)."""
    if not isinstance(data, dict):
        raise ScenarioError("scenario must be a mapping")
    name = str(data.get("name") or "Unnamed Scenario")
    description = str(data.get("description") or "")
    raw_phases = data.get("phases")
    if not isinstance(raw_phases, list) or not raw_phases:
        raise ScenarioError("scenario must have at least one entry in 'phases'")
    phases = tuple(_parse_phase(i, p) for i, p in enumerate(raw_phases))
    return Scenario(name=name, description=description, phases=phases)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_phase(index: int, raw: Any) -> Phase:
    if not isinstance(raw, dict):
        raise ScenarioError(f"phase {index} must be a mapping, got {type(raw).__name__}")

    name = str(raw.get("name") or f"Phase {index + 1}")

    # Accept either "duration" (human string e.g. "30s") or "duration_ms"
    # (pre-parsed integer milliseconds, produced by Phase.to_dict()).
    raw_dur = raw.get("duration") if "duration" in raw else raw.get("duration_ms")
    if raw_dur is None:
        raise ScenarioError(f"phase {index} ({name!r}) is missing 'duration'")
    try:
        duration_ms = parse_duration_ms(raw_dur, kind="duration")
    except Exception as exc:
        raise ScenarioError(f"phase {index} ({name!r}) bad duration: {exc}") from exc
    if duration_ms <= 0:
        raise ScenarioError(f"phase {index} ({name!r}) duration must be > 0")

    # Resolve base values from an optional 'profile' key
    bw: Any = 0
    lat: Any = 0
    loss: Any = 0.0
    jit: Any = 0

    raw_profile = raw.get("profile")
    if isinstance(raw_profile, str):
        try:
            p = get_builtin_profile(raw_profile)
            bw, lat, loss, jit = p.bandwidth_bps, p.latency_ms, p.loss_pct, p.jitter_ms
        except ProfileError as exc:
            raise ScenarioError(f"phase {index} ({name!r}): {exc}") from exc
    elif isinstance(raw_profile, dict):
        bw = raw_profile.get("bandwidth_bps") or raw_profile.get("bandwidth", 0)
        lat = raw_profile.get("latency_ms") or raw_profile.get("latency", 0)
        loss = raw_profile.get("loss_pct") or raw_profile.get("loss", 0.0)
        jit = raw_profile.get("jitter_ms") or raw_profile.get("jitter", 0)

    # Phase-level keys override profile defaults
    if "bandwidth_bps" in raw or "bandwidth" in raw:
        bw = raw.get("bandwidth_bps", raw.get("bandwidth", bw))
    if "latency_ms" in raw or "latency" in raw:
        lat = raw.get("latency_ms", raw.get("latency", lat))
    if "loss_pct" in raw or "loss" in raw:
        loss = raw.get("loss_pct", raw.get("loss", loss))
    if "jitter_ms" in raw or "jitter" in raw:
        jit = raw.get("jitter_ms", raw.get("jitter", jit))

    try:
        bandwidth_bps = parse_bandwidth(bw)
        latency_ms = parse_latency(lat)
        # loss_pct can be a fraction (0.02) or percentage string ("5%") or integer (5)
        loss_pct = _parse_loss_value(loss)
        jitter_ms = parse_jitter(jit)
    except Exception as exc:
        raise ScenarioError(f"phase {index} ({name!r}) has invalid settings: {exc}") from exc

    return Phase(
        name=name,
        duration_ms=duration_ms,
        bandwidth_bps=bandwidth_bps,
        latency_ms=latency_ms,
        loss_pct=loss_pct,
        jitter_ms=jitter_ms,
    )


def _parse_loss_value(raw: Any) -> float:
    """Parse loss from either 0.0-1.0 fraction or a string/int percentage."""
    if raw is None:
        return 0.0
    if isinstance(raw, float) and raw <= 1.0:
        # Already stored as fraction (e.g. 0.02 from a profiles.json)
        return max(0.0, raw)
    # Integer (e.g. 5 → 5%) or string ("5%") — delegate to parse_loss
    return parse_loss(raw)


def _require_yaml() -> None:
    try:
        import yaml  # noqa: F401
    except ImportError as exc:
        raise ScenarioError(
            "pyyaml is required for scenario files — install it with: "
            "pip install 'netshape[scenarios]'"
        ) from exc
