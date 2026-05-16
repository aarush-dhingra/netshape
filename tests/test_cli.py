"""Tests for netshape.cli using Typer's test runner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from netshape.cli import app

runner = CliRunner()


class TestVersion:
    def test_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_short_version_flag(self) -> None:
        result = runner.invoke(app, ["-v"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestHelp:
    def test_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Network throttling" in result.output

    def test_start_help(self) -> None:
        result = runner.invoke(app, ["start", "--help"])
        assert result.exit_code == 0
        assert "--profile" in result.output
        assert "--bandwidth" in result.output
        assert "--dry-run" in result.output

    def test_profiles_help(self) -> None:
        result = runner.invoke(app, ["profiles", "--help"])
        assert result.exit_code == 0


class TestListProfiles:
    def test_lists_builtin_profiles(self) -> None:
        result = runner.invoke(app, ["profiles"])
        assert result.exit_code == 0
        assert "3g" in result.output
        assert "4g" in result.output
        assert "dial-up" in result.output
        assert "offline" in result.output


class TestStartDryRun:
    def test_dry_run_with_profile(self) -> None:
        result = runner.invoke(app, ["start", "--profile", "3g", "--dry-run"])
        assert result.exit_code == 0
        assert "Dry run" in result.output

    def test_dry_run_with_custom_values(self) -> None:
        result = runner.invoke(app, ["start", "--bandwidth", "500kbps", "--dry-run"])
        assert result.exit_code == 0
        assert "Dry run" in result.output


class TestStatus:
    @patch("netshape.core.status")
    def test_status_inactive(self, mock_status: MagicMock) -> None:
        from netshape.core import StatusResult
        mock_status.return_value = StatusResult(active=False)

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "NORMAL" in result.output

    @patch("netshape.core.status")
    def test_status_active(self, mock_status: MagicMock) -> None:
        from netshape.core import StatusResult
        mock_status.return_value = StatusResult(
            active=True, profile="3g", bandwidth_bps=400_000,
            latency_ms=200, loss_pct=0.01, jitter_ms=20,
        )

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "THROTTLED" in result.output
        assert "3g" in result.output


class TestStop:
    @patch("netshape.core.stop")
    def test_stop(self, mock_stop: MagicMock) -> None:
        result = runner.invoke(app, ["stop"])
        assert result.exit_code == 0
        assert "stopped" in result.output.lower()
        mock_stop.assert_called_once()


class TestCleanup:
    @patch("netshape.core.cleanup", return_value=2)
    def test_cleanup_finds_rules(self, mock_cleanup: MagicMock) -> None:
        result = runner.invoke(app, ["cleanup"])
        assert result.exit_code == 0
        assert "2" in result.output

    @patch("netshape.core.cleanup", return_value=0)
    def test_cleanup_no_rules(self, mock_cleanup: MagicMock) -> None:
        result = runner.invoke(app, ["cleanup"])
        assert result.exit_code == 0
        assert "clean" in result.output.lower()


class TestProfileSubcommands:
    def test_profile_save_and_delete(self, tmp_netshape_dir: Path) -> None:
        with patch("netshape.profiles._netshape_dir", return_value=tmp_netshape_dir):
            result = runner.invoke(app, [
                "profile", "save", "test-prof",
                "--bandwidth", "800kbps",
                "--latency", "120ms",
                "--loss", "1%",
            ])
            assert result.exit_code == 0
            assert "saved" in result.output.lower()

            result = runner.invoke(app, ["profile", "delete", "test-prof"])
            assert result.exit_code == 0
            assert "deleted" in result.output.lower()

    def test_profile_export(self) -> None:
        result = runner.invoke(app, ["profile", "export", "3g"])
        assert result.exit_code == 0
        assert '"bandwidth"' in result.output
        assert '"latency"' in result.output
