"""Tests for netshape.core using mocked backends."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from netshape.core import (
    AlreadyActiveError,
    PrivilegeError,
    cleanup,
    start,
    status,
    stop,
)
from netshape.logging_setup import NetShapeLogger
from netshape.platforms.base import ThrottleBackend
from netshape.state import StateManager


class MockBackend(ThrottleBackend):
    """A fake backend that records calls without touching the OS."""

    def __init__(self, *, privileged: bool = True, active: bool = False) -> None:
        self._privileged = privileged
        self._active = active
        self.start_calls: list[dict] = []
        self.stop_calls = 0
        self.cleanup_calls = 0

    def start(
        self,
        bandwidth_bps: int,
        latency_ms: int,
        loss_pct: float,
        jitter_ms: int,
        interface: str | None = None,
    ) -> list[str]:
        self.start_calls.append({
            "bandwidth_bps": bandwidth_bps,
            "latency_ms": latency_ms,
            "loss_pct": loss_pct,
            "jitter_ms": jitter_ms,
            "interface": interface,
        })
        self._active = True
        return [f"mock-rule bw={bandwidth_bps}"]

    def stop(self) -> None:
        self.stop_calls += 1
        self._active = False

    def is_active(self) -> bool:
        return self._active

    def cleanup(self) -> int:
        self.cleanup_calls += 1
        self._active = False
        return 1

    def check_privileges(self) -> bool:
        return self._privileged

    def detect_vpn(self) -> list[str]:
        return []

    def get_default_interface(self) -> str | None:
        return "eth0"


class TestStart:
    def test_start_with_profile(self, tmp_netshape_dir: Path) -> None:
        backend = MockBackend()
        state = StateManager(base_dir=tmp_netshape_dir)
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)

        resolved, rules = start(
            profile="3g", backend=backend, state_mgr=state, logger=logger,
        )
        assert resolved.name == "3g"
        assert resolved.bandwidth_bps == 400_000
        assert len(rules) == 1
        assert backend.start_calls[0]["bandwidth_bps"] == 400_000
        assert state.is_active()

    def test_start_with_custom_values(self, tmp_netshape_dir: Path) -> None:
        backend = MockBackend()
        state = StateManager(base_dir=tmp_netshape_dir)
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)

        resolved, _ = start(
            bandwidth="500kbps", latency="100ms", loss="2%",
            backend=backend, state_mgr=state, logger=logger,
        )
        assert resolved.bandwidth_bps == 500_000
        assert resolved.latency_ms == 100

    def test_start_requires_privileges(self, tmp_netshape_dir: Path) -> None:
        backend = MockBackend(privileged=False)
        state = StateManager(base_dir=tmp_netshape_dir)
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)

        with pytest.raises(PrivilegeError, match="admin privileges"):
            start(profile="3g", backend=backend, state_mgr=state, logger=logger)

    def test_start_refuses_when_already_active(self, tmp_netshape_dir: Path) -> None:
        backend = MockBackend()
        state = StateManager(base_dir=tmp_netshape_dir)
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)

        start(profile="3g", backend=backend, state_mgr=state, logger=logger)

        with pytest.raises(AlreadyActiveError, match="already active"):
            start(profile="4g", backend=backend, state_mgr=state, logger=logger)

    def test_start_force_overrides_active(self, tmp_netshape_dir: Path) -> None:
        backend = MockBackend()
        state = StateManager(base_dir=tmp_netshape_dir)
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)

        start(profile="3g", backend=backend, state_mgr=state, logger=logger)
        resolved, _ = start(
            profile="4g", force=True, backend=backend, state_mgr=state, logger=logger,
        )
        assert resolved.name == "4g"
        assert backend.stop_calls >= 1

    def test_start_dry_run(self, tmp_netshape_dir: Path) -> None:
        backend = MockBackend(privileged=False)  # doesn't matter for dry run
        state = StateManager(base_dir=tmp_netshape_dir)
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)

        resolved, rules = start(
            profile="3g", dry_run=True,
            backend=backend, state_mgr=state, logger=logger,
        )
        assert resolved.name == "3g"
        assert "[dry-run]" in rules[0]
        assert len(backend.start_calls) == 0
        assert not state.is_active()

    def test_start_auto_cleans_stale_state(self, tmp_netshape_dir: Path) -> None:
        backend = MockBackend()
        state = StateManager(base_dir=tmp_netshape_dir)
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)

        # Write stale state with a dead PID
        import json
        state._ensure_dir()
        with open(state.state_path, "w") as f:
            json.dump({"active": True, "pid": 999999999, "profile": "old"}, f)

        resolved, _ = start(
            profile="3g", backend=backend, state_mgr=state, logger=logger,
        )
        assert resolved.name == "3g"
        assert backend.cleanup_calls >= 1


class TestStop:
    def test_stop_clears_state(self, tmp_netshape_dir: Path) -> None:
        backend = MockBackend()
        state = StateManager(base_dir=tmp_netshape_dir)
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)

        start(profile="3g", backend=backend, state_mgr=state, logger=logger)
        assert state.is_active()

        stop(backend=backend, state_mgr=state, logger=logger)
        assert not state.is_active()
        assert backend.stop_calls >= 1

    def test_stop_idempotent(self, tmp_netshape_dir: Path) -> None:
        backend = MockBackend()
        state = StateManager(base_dir=tmp_netshape_dir)
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)

        stop(backend=backend, state_mgr=state, logger=logger)  # nothing active
        stop(backend=backend, state_mgr=state, logger=logger)  # still fine


class TestStatus:
    def test_status_when_inactive(self, tmp_netshape_dir: Path) -> None:
        state = StateManager(base_dir=tmp_netshape_dir)
        result = status(state_mgr=state)
        assert result.active is False

    def test_status_when_active(self, tmp_netshape_dir: Path) -> None:
        backend = MockBackend()
        state = StateManager(base_dir=tmp_netshape_dir)
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)

        start(profile="3g", backend=backend, state_mgr=state, logger=logger)
        result = status(state_mgr=state)
        assert result.active is True
        assert result.profile == "3g"
        assert result.bandwidth_bps == 400_000

    def test_status_detects_stale(self, tmp_netshape_dir: Path) -> None:
        import json
        state = StateManager(base_dir=tmp_netshape_dir)
        state._ensure_dir()
        with open(state.state_path, "w") as f:
            json.dump({"active": True, "pid": 999999999, "profile": "3g",
                        "bandwidth_bps": 400000, "latency_ms": 200,
                        "loss_pct": 0.01, "jitter_ms": 20}, f)

        result = status(state_mgr=state)
        assert result.active is True
        assert result.stale is True


class TestCleanup:
    def test_cleanup_removes_rules(self, tmp_netshape_dir: Path) -> None:
        backend = MockBackend()
        state = StateManager(base_dir=tmp_netshape_dir)
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)

        start(profile="3g", backend=backend, state_mgr=state, logger=logger)
        count = cleanup(backend=backend, state_mgr=state, logger=logger)
        assert count >= 1
        assert not state.is_active()

    def test_cleanup_idempotent(self, tmp_netshape_dir: Path) -> None:
        backend = MockBackend()
        state = StateManager(base_dir=tmp_netshape_dir)
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)

        cleanup(backend=backend, state_mgr=state, logger=logger)
        cleanup(backend=backend, state_mgr=state, logger=logger)
