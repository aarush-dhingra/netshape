"""State persistence for active throttle sessions."""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _netshape_dir() -> Path:
    return Path.home() / ".netshape"


def _state_path() -> Path:
    return _netshape_dir() / "state.json"


class StateManager:
    """Manages the ~/.netshape/state.json file for tracking active throttle sessions."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or _netshape_dir()
        self.state_path = self.base_dir / "state.json"

    def _ensure_dir(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def write_state(
        self,
        profile: str | None,
        bandwidth_bps: int,
        latency_ms: int,
        loss_pct: float,
        jitter_ms: int,
        rules: list[str],
        interface: str | None = None,
    ) -> None:
        """Write the current throttle state atomically (write to temp, then rename)."""
        self._ensure_dir()

        state: dict[str, Any] = {
            "active": True,
            "profile": profile,
            "bandwidth_bps": bandwidth_bps,
            "latency_ms": latency_ms,
            "loss_pct": loss_pct,
            "jitter_ms": jitter_ms,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "platform": _current_platform(),
            "interface": interface,
            "rules_applied": rules,
        }

        # Atomic write: write to temp file in the same directory, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.base_dir), suffix=".tmp", prefix="state_"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(state, f, indent=2)
            # On Windows, os.replace handles atomic rename
            os.replace(tmp_path, str(self.state_path))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def read_state(self) -> dict[str, Any] | None:
        """Read the current state. Returns None if no state file exists or it's corrupt."""
        if not self.state_path.exists():
            return None
        try:
            with open(self.state_path) as f:
                return json.load(f)  # type: ignore[no-any-return]
        except (json.JSONDecodeError, OSError):
            return None

    def clear_state(self) -> None:
        """Remove the state file."""
        try:
            self.state_path.unlink(missing_ok=True)
        except OSError:
            pass

    def is_active(self) -> bool:
        """Check if a throttle session is currently recorded as active."""
        state = self.read_state()
        return state is not None and state.get("active", False)

    def detect_stale_state(self) -> bool:
        """Check if a state file exists but its PID is no longer alive (crashed session)."""
        state = self.read_state()
        if state is None or not state.get("active", False):
            return False
        pid = state.get("pid")
        if pid is None:
            return True
        return not is_pid_alive(pid)

    def get_active_pid(self) -> int | None:
        """Return the PID of the active session, or None."""
        state = self.read_state()
        if state is None:
            return None
        return state.get("pid")  # type: ignore[no-any-return]


def is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running. Cross-platform."""
    if os.name == "nt":
        return _is_pid_alive_windows(pid)
    else:
        return _is_pid_alive_unix(pid)


def _is_pid_alive_unix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but we don't own it


def _is_pid_alive_windows(pid: int) -> bool:
    import ctypes
    import ctypes.wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle == 0:
        return False

    try:
        exit_code = ctypes.wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return exit_code.value == STILL_ACTIVE
        return False
    finally:
        kernel32.CloseHandle(handle)


def _current_platform() -> str:
    import sys
    if sys.platform == "darwin":
        return "macos"
    elif sys.platform == "win32":
        return "windows"
    elif sys.platform.startswith("linux"):
        return "linux"
    return sys.platform
