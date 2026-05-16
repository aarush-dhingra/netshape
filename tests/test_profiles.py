from __future__ import annotations

import pytest

from netshape.profiles import (
    ProfileError,
    get_builtin_profile,
    list_builtin_profiles,
    load_builtin_profiles,
    resolve_settings,
    validate_profile_name,
)


def test_loads_all_builtin_profiles() -> None:
    profiles = load_builtin_profiles()

    assert len(profiles) == 12
    assert "3g" in profiles
    assert profiles["3g"].bandwidth_bps > 0
    assert profiles["3g"].latency_ms > 0
    assert 0 <= profiles["3g"].loss_pct <= 1


def test_list_builtin_profiles_is_sorted() -> None:
    profiles = list_builtin_profiles()

    assert [profile.name for profile in profiles] == sorted(profile.name for profile in profiles)


def test_get_builtin_profile_by_name() -> None:
    profile = get_builtin_profile("4g")

    assert profile.name == "4g"
    assert profile.description


def test_unknown_profile_raises() -> None:
    with pytest.raises(ProfileError, match="unknown profile"):
        get_builtin_profile("does-not-exist")


@pytest.mark.parametrize("name", ["3g", "mumbai-4g", "office_wifi", "fiber"])
def test_validate_profile_name_accepts_valid_names(name: str) -> None:
    assert validate_profile_name(name) == name


@pytest.mark.parametrize("name", ["", "Mumbai", "-edge", "bad name", "bad.name"])
def test_validate_profile_name_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ProfileError):
        validate_profile_name(name)


def test_resolve_settings_from_profile() -> None:
    profile = get_builtin_profile("3g")
    settings = resolve_settings(profile="3g")

    assert settings.profile == "3g"
    assert settings.bandwidth_bps == profile.bandwidth_bps
    assert settings.latency_ms == profile.latency_ms
    assert settings.loss_pct == profile.loss_pct
    assert settings.jitter_ms == profile.jitter_ms


def test_resolve_settings_applies_overrides() -> None:
    settings = resolve_settings(
        profile="3g",
        bandwidth="100kbps",
        latency="750ms",
        loss="5%",
        jitter="30ms",
    )

    assert settings.profile == "3g"
    assert settings.bandwidth_bps == 100_000
    assert settings.latency_ms == 750
    assert settings.loss_pct == pytest.approx(0.05)
    assert settings.jitter_ms == 30


def test_resolve_settings_without_profile_uses_zero_defaults() -> None:
    settings = resolve_settings(latency="100ms")

    assert settings.profile is None
    assert settings.bandwidth_bps == 0
    assert settings.latency_ms == 100
    assert settings.loss_pct == 0
    assert settings.jitter_ms == 0
