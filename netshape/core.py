"""Core orchestration: start, stop, status, cleanup flows."""

from __future__ import annotations

import atexit
import os
import signal
import sys
from dataclasses import dataclass
from typing import Any

from netshape.config import ConfigManager
from netshape.logging_setup import NetShapeLogger
from netshape.platforms import get_backend
from netshape.platforms.base import ThrottleBackend
from netshape.profiles import ResolvedProfile, resolve_profile
from netshape.state import StateManager
from netshape.units import validate_ranges


class NetShapeError(Exception):
    """Base exception for NetShape operational errors."""


class PrivilegeError(NetShapeError):
    """Raised when the user lacks admin/root privileges."""


class AlreadyActiveError(NetShapeError):
    """Raised when throttling is already active."""


@dataclass
class StatusResult:
    active: bool
    profile: str | None = None
    bandwidth_bps: int = 0
    latency_ms: int = 0
    loss_pct: float = 0.0
    jitter_ms: int = 0
    started_at: str | None = None
    stale: bool = False


_cleanup_registered = False


def _register_signal_handlers(backend: ThrottleBackend, state: StateManager) -> None:
    """Register signal handlers so Ctrl+C and SIGTERM trigger cleanup."""
    global _cleanup_registered
    if _cleanup_registered:
        return

    def handler(signum: int, frame: Any) -> None:
        try:
            backend.stop()
        except Exception:
            pass
        state.clear_state()
        sys.exit(0)

    if os.name != "nt":
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        try:
            signal.signal(signal.SIGHUP, handler)  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
    else:
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    atexit.register(lambda: (_safe_stop(backend), state.clear_state()))
    _cleanup_registered = True


def _safe_stop(backend: ThrottleBackend) -> None:
    try:
        backend.stop()
    except Exception:
        pass


def start(
    profile: str | None = None,
    bandwidth: str | None = None,
    latency: str | None = None,
    loss: str | None = None,
    jitter: str | None = None,
    force: bool = False,
    interface: str | None = None,
    dry_run: bool = False,
    backend: ThrottleBackend | None = None,
    state_mgr: StateManager | None = None,
    logger: NetShapeLogger | None = None,
) -> tuple[ResolvedProfile, list[str]]:
    """Start network throttling.

    Returns the resolved profile and the list of rules applied.
    """
    _backend = backend or get_backend()
    _state = state_mgr or StateManager()
    _logger = logger or NetShapeLogger()

    if not dry_run and not _backend.check_privileges():
        if sys.platform == "win32":
            raise PrivilegeError(
                "Network throttling requires admin privileges.\n"
                "Run your terminal as Administrator, then try again."
            )
        else:
            raise PrivilegeError(
                "Network throttling requires admin privileges.\n"
                "Run: sudo netshape start ..."
            )

    resolved = resolve_profile(
        profile_name=profile,
        bandwidth=bandwidth,
        latency=latency,
        loss=loss,
        jitter=jitter,
    )

    validate_ranges(
        resolved.bandwidth_bps,
        resolved.latency_ms,
        resolved.loss_pct,
        resolved.jitter_ms,
    )

    if not dry_run:
        # Handle stale/active state
        if _state.detect_stale_state():
            _logger.log("AUTO_CLEANUP", reason="stale state from crashed session")
            _backend.cleanup()
            _state.clear_state()
        elif _state.is_active() and not force:
            active = _state.read_state()
            active_profile = active.get("profile", "unknown") if active else "unknown"
            raise AlreadyActiveError(
                f"Throttle is already active (profile: {active_profile}).\n"
                f"Use 'netshape stop' first, or 'netshape start --force' to override."
            )
        elif _state.is_active() and force:
            _backend.stop()
            _state.clear_state()

    vpn_interfaces = _backend.detect_vpn()

    if dry_run:
        return resolved, [f"[dry-run] Would apply: {resolved}"]

    _register_signal_handlers(_backend, _state)

    rules: list[str] = []
    try:
        rules = _backend.start(
            bandwidth_bps=resolved.bandwidth_bps,
            latency_ms=resolved.latency_ms,
            loss_pct=resolved.loss_pct,
            jitter_ms=resolved.jitter_ms,
            interface=interface,
        )
    except Exception:
        # Rollback: if start partially applied, clean up
        try:
            _backend.stop()
        except Exception:
            pass
        raise

    _state.write_state(
        profile=resolved.name,
        bandwidth_bps=resolved.bandwidth_bps,
        latency_ms=resolved.latency_ms,
        loss_pct=resolved.loss_pct,
        jitter_ms=resolved.jitter_ms,
        rules=rules,
        interface=interface,
    )

    _logger.log(
        "START",
        profile=resolved.name,
        bandwidth_bps=resolved.bandwidth_bps,
        latency_ms=resolved.latency_ms,
        loss_pct=resolved.loss_pct,
        jitter_ms=resolved.jitter_ms,
    )

    return resolved, rules


def stop(
    backend: ThrottleBackend | None = None,
    state_mgr: StateManager | None = None,
    logger: NetShapeLogger | None = None,
) -> None:
    """Stop network throttling. Idempotent — safe to call even when nothing is active."""
    _backend = backend or get_backend()
    _state = state_mgr or StateManager()
    _logger = logger or NetShapeLogger()

    try:
        _backend.stop()
    except Exception:
        pass

    _state.clear_state()
    _logger.log("STOP")


def status(
    state_mgr: StateManager | None = None,
) -> StatusResult:
    """Get the current throttle status."""
    _state = state_mgr or StateManager()
    state = _state.read_state()

    if state is None or not state.get("active", False):
        return StatusResult(active=False)

    is_stale = _state.detect_stale_state()

    return StatusResult(
        active=True,
        profile=state.get("profile"),
        bandwidth_bps=state.get("bandwidth_bps", 0),
        latency_ms=state.get("latency_ms", 0),
        loss_pct=state.get("loss_pct", 0.0),
        jitter_ms=state.get("jitter_ms", 0),
        started_at=state.get("started_at"),
        stale=is_stale,
    )


def cleanup(
    backend: ThrottleBackend | None = None,
    state_mgr: StateManager | None = None,
    logger: NetShapeLogger | None = None,
) -> int:
    """Emergency cleanup: remove all NetShape OS rules and clear state.

    Returns the number of rules removed.
    """
    _backend = backend or get_backend()
    _state = state_mgr or StateManager()
    _logger = logger or NetShapeLogger()

    count = 0
    try:
        count = _backend.cleanup()
    except Exception:
        pass

    _state.clear_state()
    _logger.log("CLEANUP", rules_removed=count)
    return count
