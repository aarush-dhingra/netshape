"""NetShape — Network throttling in one command.

Usage as a library:

    import netshape

    # Context manager (recommended)
    with netshape.throttle(profile="3g"):
        run_tests()

    # Manual start/stop
    netshape.start(profile="3g")
    netshape.stop()

    # Custom values
    with netshape.throttle(bandwidth="250kbps", latency="300ms", loss="2%"):
        run_tests()

    # Speed test
    result = netshape.speed_test()

    # Status
    state = netshape.status()

    # Profiles
    netshape.profiles.list_all()
"""

from __future__ import annotations

__version__ = "0.1.0"

from netshape.core import cleanup as cleanup
from netshape.core import start as _core_start
from netshape.core import status as status
from netshape.core import stop as stop
from netshape.profiles import (
    ResolvedProfile,
    delete_custom,
    list_all,
    list_builtin,
    list_custom,
    resolve_profile,
    save_custom,
)
from netshape.speed_test import SpeedTestResult
from netshape.speed_test import run_speed_test as speed_test


class throttle:
    """Context manager for network throttling.

    Usage:
        with netshape.throttle(profile="3g"):
            # everything here runs under 3G conditions
            ...

        with netshape.throttle(bandwidth="250kbps", latency="300ms"):
            ...
    """

    def __init__(
        self,
        profile: str | None = None,
        bandwidth: str | None = None,
        latency: str | None = None,
        loss: str | None = None,
        jitter: str | None = None,
    ) -> None:
        self.profile = profile
        self.bandwidth = bandwidth
        self.latency = latency
        self.loss = loss
        self.jitter = jitter
        self._resolved: ResolvedProfile | None = None

    def __enter__(self) -> throttle:
        resolved, _ = _core_start(
            profile=self.profile,
            bandwidth=self.bandwidth,
            latency=self.latency,
            loss=self.loss,
            jitter=self.jitter,
        )
        self._resolved = resolved
        return self

    def __exit__(self, *args: object) -> bool:
        stop()
        return False

    @property
    def resolved(self) -> ResolvedProfile | None:
        return self._resolved


def start(
    profile: str | None = None,
    bandwidth: str | None = None,
    latency: str | None = None,
    loss: str | None = None,
    jitter: str | None = None,
    force: bool = False,
) -> ResolvedProfile:
    """Start network throttling. Returns the resolved profile."""
    resolved, _ = _core_start(
        profile=profile,
        bandwidth=bandwidth,
        latency=latency,
        loss=loss,
        jitter=jitter,
        force=force,
    )
    return resolved


class profiles:
    """Namespace for profile management functions."""

    @staticmethod
    def list() -> dict:
        """List all profiles (built-in + custom)."""
        return list_all()

    @staticmethod
    def save(
        name: str,
        bandwidth: str,
        latency: str,
        loss: str = "0%",
        jitter: str = "0ms",
        description: str = "",
    ) -> None:
        """Save a custom profile."""
        save_custom(name, bandwidth, latency, loss, jitter, description)

    @staticmethod
    def delete(name: str) -> None:
        """Delete a custom profile."""
        delete_custom(name)

    @staticmethod
    def load(name: str) -> ResolvedProfile:
        """Load and resolve a profile by name."""
        return resolve_profile(profile_name=name)
