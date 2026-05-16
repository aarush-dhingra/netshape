"""Abstract base class for platform-specific throttle backends."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ThrottleBackend(ABC):
    """Interface that every platform backend must implement."""

    @abstractmethod
    def start(
        self,
        bandwidth_bps: int,
        latency_ms: int,
        loss_pct: float,
        jitter_ms: int,
        interface: str | None = None,
    ) -> list[str]:
        """Apply throttling rules at the OS level.

        Returns a list of string descriptions of the rules/commands applied,
        for recording in the state file.
        """

    @abstractmethod
    def stop(self) -> None:
        """Remove all throttling rules applied by NetShape."""

    @abstractmethod
    def is_active(self) -> bool:
        """Check if NetShape throttling rules are currently applied at the OS level."""

    @abstractmethod
    def cleanup(self) -> int:
        """Remove any lingering NetShape rules (crash recovery).

        Returns the count of rules removed. Must be idempotent and never raise.
        """

    @abstractmethod
    def check_privileges(self) -> bool:
        """Check if the current process has sufficient privileges (admin/root)."""

    @abstractmethod
    def detect_vpn(self) -> list[str]:
        """Return a list of active VPN interface names (for warning the user)."""

    @abstractmethod
    def get_default_interface(self) -> str | None:
        """Detect the default network interface."""
