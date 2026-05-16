"""Tests for netshape.profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from netshape.profiles import (
    ProfileError,
    delete_custom,
    list_builtin,
    resolve_profile,
    save_custom,
)


class TestListBuiltin:
    def test_returns_all_12_profiles(self) -> None:
        profiles = list_builtin()
        assert len(profiles) == 12

    def test_known_profiles_exist(self) -> None:
        profiles = list_builtin()
        expected = {
            "3g", "3g-fast", "3g-slow", "4g", "lte", "edge",
            "2g", "slow-wifi", "flaky-wifi", "satellite", "dial-up", "offline",
        }
        assert set(profiles.keys()) == expected

    def test_each_profile_has_required_fields(self) -> None:
        profiles = list_builtin()
        for name, data in profiles.items():
            assert "bandwidth" in data, f"{name} missing bandwidth"
            assert "latency" in data, f"{name} missing latency"
            assert "loss" in data, f"{name} missing loss"
            assert "jitter" in data, f"{name} missing jitter"
            assert "description" in data, f"{name} missing description"


class TestResolveProfile:
    def test_resolve_builtin(self) -> None:
        p = resolve_profile(profile_name="3g")
        assert p.name == "3g"
        assert p.bandwidth_bps == 400_000
        assert p.latency_ms == 200
        assert abs(p.loss_pct - 0.01) < 1e-9
        assert p.jitter_ms == 20

    def test_resolve_with_override(self) -> None:
        p = resolve_profile(profile_name="3g", bandwidth="100kbps")
        assert p.name == "3g"
        assert p.bandwidth_bps == 100_000
        assert p.latency_ms == 200  # unchanged

    def test_resolve_custom_values(self) -> None:
        p = resolve_profile(bandwidth="250kbps", latency="300ms", loss="2%")
        assert p.name is None
        assert p.bandwidth_bps == 250_000
        assert p.latency_ms == 300
        assert abs(p.loss_pct - 0.02) < 1e-9

    def test_resolve_custom_defaults_optional(self) -> None:
        p = resolve_profile(bandwidth="500kbps")
        assert p.latency_ms == 0
        assert p.loss_pct == 0.0
        assert p.jitter_ms == 0

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(ProfileError, match="Unknown profile"):
            resolve_profile(profile_name="nonexistent-profile")

    def test_no_profile_no_bandwidth_raises(self) -> None:
        with pytest.raises(ProfileError, match="--profile or at least --bandwidth"):
            resolve_profile()


class TestSaveAndDeleteCustom:
    def test_save_and_resolve(self, tmp_netshape_dir: Path) -> None:
        save_custom(
            "test-profile", "800kbps", "120ms", "1%", "10ms",
            description="Test", base_dir=tmp_netshape_dir,
        )
        # Verify the file exists
        assert (tmp_netshape_dir / "profiles" / "test-profile.json").exists()

    def test_save_invalid_name_raises(self, tmp_netshape_dir: Path) -> None:
        with pytest.raises(ProfileError, match="Invalid profile name"):
            save_custom(
                "bad name!", "800kbps", "120ms", "1%", "10ms",
                base_dir=tmp_netshape_dir,
            )

    def test_save_builtin_name_raises(self, tmp_netshape_dir: Path) -> None:
        with pytest.raises(ProfileError, match="Cannot overwrite"):
            save_custom(
                "3g", "800kbps", "120ms", "1%", "10ms",
                base_dir=tmp_netshape_dir,
            )

    def test_delete_custom(self, tmp_netshape_dir: Path) -> None:
        save_custom(
            "to-delete", "800kbps", "120ms", "1%", "10ms",
            base_dir=tmp_netshape_dir,
        )
        delete_custom("to-delete", base_dir=tmp_netshape_dir)
        assert not (tmp_netshape_dir / "profiles" / "to-delete.json").exists()

    def test_delete_builtin_raises(self, tmp_netshape_dir: Path) -> None:
        with pytest.raises(ProfileError, match="Cannot delete"):
            delete_custom("3g", base_dir=tmp_netshape_dir)

    def test_delete_nonexistent_raises(self, tmp_netshape_dir: Path) -> None:
        with pytest.raises(ProfileError, match="not found"):
            delete_custom("ghost", base_dir=tmp_netshape_dir)
