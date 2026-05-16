"""Windows throttle backend using netsh/PowerShell QoS policies."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from typing import Any

from netshape.platforms.base import ThrottleBackend

_POLICY_PREFIX = "NetShape"


def _run_powershell(command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a PowerShell command and return the result."""
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=check,
    )


def _run_powershell_json(command: str) -> list[dict[str, Any]]:
    """Run a PowerShell command that outputs JSON and parse it."""
    result = _run_powershell(f"{command} | ConvertTo-Json -Depth 3", check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return []

    import json
    raw = json.loads(result.stdout)
    if isinstance(raw, dict):
        return [raw]
    return raw  # type: ignore[no-any-return]


class WindowsBackend(ThrottleBackend):
    """Windows backend using New-NetQosPolicy for bandwidth throttling.

    Limitations (v1):
    - Only bandwidth throttling is supported via QoS policies.
    - Latency injection, packet loss, and jitter are NOT supported.
    - These will be added in a future version with a native WFP binary.
    """

    def start(
        self,
        bandwidth_bps: int,
        latency_ms: int,
        loss_pct: float,
        jitter_ms: int,
        interface: str | None = None,
    ) -> list[str]:
        rules_applied: list[str] = []

        unsupported: list[str] = []
        if latency_ms > 0:
            unsupported.append(f"latency ({latency_ms}ms)")
        if loss_pct > 0:
            unsupported.append(f"packet loss ({loss_pct * 100:.1f}%)")
        if jitter_ms > 0:
            unsupported.append(f"jitter ({jitter_ms}ms)")
        self._unsupported_params = unsupported

        # Remove any existing NetShape policies first
        self._remove_all_policies()

        if bandwidth_bps > 0:
            policy_name = f"{_POLICY_PREFIX}-Throttle"
            cmd = (
                f'New-NetQosPolicy -Name "{policy_name}" '
                f"-ThrottleRateActionBitsPerSecond {bandwidth_bps} "
                f"-IPProtocolMatchCondition Both "
                f"-NetworkProfile All "
                f"-Confirm:$false"
            )
            _run_powershell(cmd)
            rules_applied.append(f"QoS Policy: {policy_name} at {bandwidth_bps} bps")

        return rules_applied

    def stop(self) -> None:
        self._remove_all_policies()

    def is_active(self) -> bool:
        policies = _run_powershell_json(
            f'Get-NetQosPolicy -Name "{_POLICY_PREFIX}-*" -ErrorAction SilentlyContinue'
        )
        return len(policies) > 0

    def cleanup(self) -> int:
        return self._remove_all_policies()

    def check_privileges(self) -> bool:
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            return False

    def detect_vpn(self) -> list[str]:
        try:
            adapters = _run_powershell_json(
                "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} "
                "| Select-Object Name, InterfaceDescription"
            )
            vpn_keywords = ["vpn", "wireguard", "tap", "tunnel", "ppp"]
            vpn_names: list[str] = []
            for adapter in adapters:
                desc = adapter.get("InterfaceDescription", "").lower()
                name = adapter.get("Name", "")
                if any(kw in desc for kw in vpn_keywords):
                    vpn_names.append(name)
            return vpn_names
        except (subprocess.SubprocessError, OSError):
            return []

    def get_default_interface(self) -> str | None:
        try:
            result = _run_powershell(
                "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' "
                "| Sort-Object RouteMetric | Select-Object -First 1).InterfaceAlias"
            )
            iface = result.stdout.strip()
            return iface if iface else None
        except (subprocess.SubprocessError, OSError):
            return None

    @property
    def unsupported_warnings(self) -> list[str]:
        """Return warnings about unsupported parameters (set after start())."""
        return getattr(self, "_unsupported_params", [])

    def _remove_all_policies(self) -> int:
        """Remove all NetShape QoS policies. Returns count removed."""
        try:
            policies = _run_powershell_json(
                f'Get-NetQosPolicy | Where-Object {{$_.Name -like "{_POLICY_PREFIX}-*"}} '
                "| Select-Object Name"
            )
            count = len(policies)
            if count > 0:
                _run_powershell(
                    f'Get-NetQosPolicy | Where-Object {{$_.Name -like "{_POLICY_PREFIX}-*"}} '
                    "| Remove-NetQosPolicy -Confirm:$false",
                    check=False,
                )
            return count
        except (subprocess.SubprocessError, OSError):
            return 0
