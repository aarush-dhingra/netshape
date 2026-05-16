"""Platform detection and backend dispatch."""

from __future__ import annotations

import sys

from netshape.platforms.base import ThrottleBackend


class PlatformNotSupportedError(Exception):
    """Raised when NetShape is run on an unsupported platform."""


def get_backend() -> ThrottleBackend:
    """Return the throttle backend for the current OS."""
    if sys.platform == "win32":
        from netshape.platforms.windows import WindowsBackend
        return WindowsBackend()
    elif sys.platform == "darwin":
        from netshape.platforms.macos import MacOSBackend
        return MacOSBackend()
    elif sys.platform.startswith("linux"):
        from netshape.platforms.linux import LinuxBackend
        return LinuxBackend()
    else:
        raise PlatformNotSupportedError(
            f"NetShape does not support '{sys.platform}'. "
            f"Supported platforms: Windows, macOS, Linux."
        )
