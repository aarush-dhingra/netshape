"""Tests for netshape.state, netshape.config, netshape.logging_setup."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from netshape.config import ConfigManager
from netshape.logging_setup import NetShapeLogger
from netshape.state import StateManager, is_pid_alive


class TestStateManager:
    def test_write_and_read_state(self, tmp_netshape_dir: Path) -> None:
        mgr = StateManager(base_dir=tmp_netshape_dir)
        mgr.write_state(
            profile="3g",
            bandwidth_bps=400_000,
            latency_ms=200,
            loss_pct=0.01,
            jitter_ms=20,
            rules=["rule1", "rule2"],
        )
        state = mgr.read_state()
        assert state is not None
        assert state["active"] is True
        assert state["profile"] == "3g"
        assert state["bandwidth_bps"] == 400_000
        assert state["latency_ms"] == 200
        assert state["loss_pct"] == 0.01
        assert state["jitter_ms"] == 20
        assert state["pid"] == os.getpid()
        assert state["rules_applied"] == ["rule1", "rule2"]

    def test_read_state_returns_none_when_missing(self, tmp_netshape_dir: Path) -> None:
        mgr = StateManager(base_dir=tmp_netshape_dir)
        assert mgr.read_state() is None

    def test_clear_state(self, tmp_netshape_dir: Path) -> None:
        mgr = StateManager(base_dir=tmp_netshape_dir)
        mgr.write_state("3g", 400_000, 200, 0.01, 20, ["rule1"])
        assert mgr.is_active()
        mgr.clear_state()
        assert not mgr.is_active()
        assert mgr.read_state() is None

    def test_clear_state_when_no_file(self, tmp_netshape_dir: Path) -> None:
        mgr = StateManager(base_dir=tmp_netshape_dir)
        mgr.clear_state()  # should not raise

    def test_is_active(self, tmp_netshape_dir: Path) -> None:
        mgr = StateManager(base_dir=tmp_netshape_dir)
        assert not mgr.is_active()
        mgr.write_state("3g", 400_000, 200, 0.01, 20, [])
        assert mgr.is_active()

    def test_detect_stale_state_with_dead_pid(self, tmp_netshape_dir: Path) -> None:
        mgr = StateManager(base_dir=tmp_netshape_dir)
        # Write state with a PID that definitely doesn't exist
        mgr.write_state("3g", 400_000, 200, 0.01, 20, [])
        # Manually patch the PID to a dead one
        state = mgr.read_state()
        assert state is not None
        state["pid"] = 999999999
        with open(mgr.state_path, "w") as f:
            json.dump(state, f)
        assert mgr.detect_stale_state() is True

    def test_detect_stale_state_with_live_pid(self, tmp_netshape_dir: Path) -> None:
        mgr = StateManager(base_dir=tmp_netshape_dir)
        mgr.write_state("3g", 400_000, 200, 0.01, 20, [])
        # Current PID is alive
        assert mgr.detect_stale_state() is False

    def test_detect_stale_state_when_no_state(self, tmp_netshape_dir: Path) -> None:
        mgr = StateManager(base_dir=tmp_netshape_dir)
        assert mgr.detect_stale_state() is False

    def test_corrupt_state_file(self, tmp_netshape_dir: Path) -> None:
        mgr = StateManager(base_dir=tmp_netshape_dir)
        mgr._ensure_dir()
        with open(mgr.state_path, "w") as f:
            f.write("not valid json {{{")
        assert mgr.read_state() is None
        assert not mgr.is_active()

    def test_get_active_pid(self, tmp_netshape_dir: Path) -> None:
        mgr = StateManager(base_dir=tmp_netshape_dir)
        assert mgr.get_active_pid() is None
        mgr.write_state("3g", 400_000, 200, 0.01, 20, [])
        assert mgr.get_active_pid() == os.getpid()

    def test_write_creates_base_dir(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / ".netshape"
        mgr = StateManager(base_dir=nested)
        mgr.write_state("3g", 400_000, 200, 0.01, 20, [])
        assert mgr.is_active()


class TestIsPidAlive:
    def test_current_pid_is_alive(self) -> None:
        assert is_pid_alive(os.getpid()) is True

    def test_nonexistent_pid(self) -> None:
        assert is_pid_alive(999999999) is False


class TestConfigManager:
    def test_default_config(self, tmp_netshape_dir: Path) -> None:
        cfg = ConfigManager(base_dir=tmp_netshape_dir)
        config = cfg.load()
        assert config["default_timeout_minutes"] is None
        assert config["speed_test_endpoint"] is None

    def test_set_and_get(self, tmp_netshape_dir: Path) -> None:
        cfg = ConfigManager(base_dir=tmp_netshape_dir)
        cfg.set("speed_test_endpoint", "https://example.com")
        assert cfg.get("speed_test_endpoint") == "https://example.com"

        # Reload from disk
        cfg2 = ConfigManager(base_dir=tmp_netshape_dir)
        assert cfg2.get("speed_test_endpoint") == "https://example.com"

    def test_corrupt_config_falls_back_to_defaults(self, tmp_netshape_dir: Path) -> None:
        config_path = tmp_netshape_dir / "config.json"
        with open(config_path, "w") as f:
            f.write("broken{{{")
        cfg = ConfigManager(base_dir=tmp_netshape_dir)
        config = cfg.load()
        assert config["default_timeout_minutes"] is None


class TestNetShapeLogger:
    def test_log_creates_file(self, tmp_netshape_dir: Path) -> None:
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)
        logger.log("START", profile="3g")
        assert (tmp_netshape_dir / "logs" / "netshape.log").exists()

    def test_log_entry_is_valid_jsonl(self, tmp_netshape_dir: Path) -> None:
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)
        logger.log("START", profile="3g", bandwidth_bps=400_000)

        with open(tmp_netshape_dir / "logs" / "netshape.log") as f:
            line = f.readline()
        entry = json.loads(line)
        assert entry["event"] == "START"
        assert entry["profile"] == "3g"
        assert "timestamp" in entry
        assert "pid" in entry

    def test_rotation(self, tmp_netshape_dir: Path) -> None:
        logger = NetShapeLogger(base_dir=tmp_netshape_dir)
        for i in range(1100):
            logger.log("TICK", count=i)

        with open(tmp_netshape_dir / "logs" / "netshape.log") as f:
            lines = f.readlines()
        assert len(lines) <= 1000
