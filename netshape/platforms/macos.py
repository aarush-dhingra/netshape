"""macOS throttle backend — stub for future implementation."""

from __future__ import annotations

from netshape.platforms.base import ThrottleBackend


class MacOSBackend(ThrottleBackend):
    """macOS backend using pfctl/dnctl. Not yet implemented."""

    def start(
        self,
        bandwidth_bps: int,
        latency_ms: int,
        loss_pct: float,
        jitter_ms: int,
        interface: str | None = None,
    ) -> list[str]:
        raise NotImplementedError(
            "macOS support is not yet implemented. "
            "Track progress at https://github.com/aarush-dhingra/netshape/issues"
        )

    def stop(self) -> None:
        raise NotImplementedError("macOS support is not yet implemented.")

    def is_active(self) -> bool:
        return False

    def cleanup(self) -> int:
        return 0

    def check_privileges(self) -> bool:
        import os
        return os.geteuid() == 0  # type: ignore[attr-defined]

    def detect_vpn(self) -> list[str]:
        return []

    def get_default_interface(self) -> str | None:
        return None
