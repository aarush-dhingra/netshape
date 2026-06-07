"""Session lifecycle management for NetShape."""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import os
import socket
import subprocess  # nosec B404 – spawning a user-provided child process is the core feature
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Generator

import psutil

from .profiles import resolve_settings
from .proxy_server import ThrottleConfig, ThrottledProxy
from .units import parse_bandwidth, parse_duration_ms, parse_jitter, parse_latency, parse_loss

DEFAULT_TRAFFIC_PORT = 8090
DEFAULT_STATE_PATH = Path.home() / ".netshape" / "state.json"
_NETSHAPE_DIR = Path.home() / ".netshape"
_RULES_FILE = _NETSHAPE_DIR / "rules.json"
_CONFIG_FILE = _NETSHAPE_DIR / "config.json"


def load_config() -> dict[str, Any]:
    """Load user preferences from ~/.netshape/config.json."""
    try:
        if _CONFIG_FILE.exists():
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:  # nosec B110
        pass
    return {}


def save_config(config: dict[str, Any]) -> None:
    """Persist user preferences to ~/.netshape/config.json."""
    try:
        _NETSHAPE_DIR.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
        _restrict_permissions(_CONFIG_FILE)
    except Exception:  # nosec B110
        pass


def is_first_run() -> bool:
    """Return True if the user has never run netshape setup / first-run wizard."""
    return not _CONFIG_FILE.exists()


def is_dashboard_enabled() -> bool:
    """Return True if the user opted in to the web dashboard."""
    cfg = load_config()
    # Default True so existing installs without config keep working.
    return bool(cfg.get("dashboard", True))


def _load_persisted_rules() -> list[dict[str, Any]]:
    """Load rules saved from a previous session, if any."""
    try:
        if _RULES_FILE.exists():
            data = json.loads(_RULES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:  # nosec B110 – corrupted/missing rules file is not fatal
        pass
    return []


def _save_persisted_rules(rules: list[dict[str, Any]]) -> None:
    """Persist the current rule set (without server-assigned IDs) to disk."""
    try:
        _NETSHAPE_DIR.mkdir(parents=True, exist_ok=True)
        clean = [{k: v for k, v in r.items() if k != "id"} for r in rules]
        _RULES_FILE.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    except Exception:  # nosec B110 – persistence failure must not crash the session
        pass


class SessionError(RuntimeError):
    """Raised when a NetShape session operation fails."""


@dataclass(frozen=True)
class SessionState:
    active: bool
    traffic_port: int
    control_port: int
    pid: int
    started_at: float
    profile: str | None = None
    bandwidth_bps: int = 0
    latency_ms: int = 0
    loss_pct: float = 0.0
    jitter_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProxyRunner:
    """Runs the asyncio proxy in a background thread."""

    def __init__(self, proxy: ThrottledProxy) -> None:
        self.proxy = proxy
        self._ready = threading.Event()
        self._done = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="netshape-proxy", daemon=True)

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise SessionError("proxy did not start within 5 seconds")
        if self._error is not None:
            raise SessionError("proxy failed to start") from self._error

    def stop(self) -> None:
        try:
            _post_json(self.proxy.control_port, "/shutdown", {})
        except OSError:
            pass
        self._done.wait(timeout=10)
        self._thread.join(timeout=1)

    def _run(self) -> None:
        async def run_proxy() -> None:
            await self.proxy.start()
            self._ready.set()
            await self.proxy.wait_closed()

        try:
            asyncio.run(run_proxy())
        except BaseException as exc:
            self._error = exc
            self._ready.set()
        finally:
            self._done.set()


def run_session(
    *,
    command: list[str],
    profile: str | None = None,
    bandwidth: str | int | float | None = None,
    latency: str | int | float | None = None,
    loss: str | int | float | None = None,
    jitter: str | int | float | None = None,
    timeout: str | int | float | None = None,
    traffic_port: int = DEFAULT_TRAFFIC_PORT,
    control_port: int | None = None,
    state_path: Path = DEFAULT_STATE_PATH,
    on_ready: Any = None,
) -> int:
    if not command:
        raise SessionError("command is required")

    settings = resolve_settings(
        profile=profile,
        bandwidth=bandwidth,
        latency=latency,
        loss=loss,
        jitter=jitter,
    )
    if traffic_port == 0:
        resolved_traffic_port = 0
    else:
        resolved_traffic_port = _find_free_port(traffic_port)

    if control_port is None:
        if traffic_port == 0:
            resolved_control_port = 0
        else:
            resolved_control_port = _find_free_port(resolved_traffic_port + 1)
    else:
        resolved_control_port = control_port

    proxy = ThrottledProxy(
        traffic_port=resolved_traffic_port,
        control_port=resolved_control_port,
        config=ThrottleConfig(
            bandwidth_bps=settings.bandwidth_bps,
            latency_ms=settings.latency_ms,
            loss_pct=settings.loss_pct,
            jitter_ms=settings.jitter_ms,
            profile=settings.profile,
        ),
    )
    runner = ProxyRunner(proxy)
    runner.start()

    state = SessionState(
        active=True,
        traffic_port=proxy.traffic_port,
        control_port=proxy.control_port,
        pid=os.getpid(),
        started_at=time.time(),
        profile=settings.profile,
        bandwidth_bps=settings.bandwidth_bps,
        latency_ms=settings.latency_ms,
        loss_pct=settings.loss_pct,
        jitter_ms=settings.jitter_ms,
    )
    write_state(state, state_path)

    # Fire the optional ready callback so the CLI can print a banner before
    # the child process starts streaming its own output.
    if on_ready is not None:
        try:
            on_ready(state)
        except Exception:  # nosec B110 – banner failure must never block the session
            pass

    # Restore rules saved from the previous session (best-effort).
    # Rules always start DISABLED so the user can consciously re-enable them.
    for rule_dict in _load_persisted_rules():
        try:
            result = _post_json(state.control_port, "/rules", rule_dict)
            rule_id = result.get("id", "")
            if rule_id:
                _patch_json(state.control_port, f"/rules/{rule_id}", {"enabled": False})
        except Exception:  # nosec B110 – a bad persisted rule must not block startup
            pass

    env = _proxy_env(os.environ.copy(), proxy.traffic_port)
    # shell=True is required on Windows so that script wrappers such as `npx`
    # (which are .cmd batch files) are found by the shell. The command list comes
    # directly from the user's CLI invocation, which is the intended use-case for
    # this developer tool — the user deliberately chose what to run.
    process = subprocess.Popen(command, env=env, shell=(sys.platform == "win32"))  # nosec B602
    timeout_seconds = None if timeout is None else parse_duration_ms(timeout, kind="timeout") / 1000
    timeout_timer: threading.Timer | None = None
    if timeout_seconds is not None and timeout_seconds > 0:
        timeout_timer = threading.Timer(timeout_seconds, _terminate_process, args=(process,))
        timeout_timer.daemon = True
        timeout_timer.start()
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        return process.wait()
    finally:
        if timeout_timer is not None:
            timeout_timer.cancel()
        runner.stop()
        clear_state(state_path)


def adjust_session(
    *,
    profile: str | None = None,
    bandwidth: str | int | float | None = None,
    latency: str | int | float | None = None,
    loss: str | int | float | None = None,
    jitter: str | int | float | None = None,
    state_path: Path = DEFAULT_STATE_PATH,
) -> dict[str, Any]:
    state = read_state(state_path)
    if state is None:
        raise SessionError("no active NetShape session")

    if profile is not None:
        settings = resolve_settings(
            profile=profile,
            bandwidth=bandwidth,
            latency=latency,
            loss=loss,
            jitter=jitter,
        )
    else:
        settings = resolve_settings(
            bandwidth=state.bandwidth_bps if bandwidth is None else bandwidth,
            latency=state.latency_ms if latency is None else latency,
            loss=state.loss_pct if loss is None else loss,
            jitter=state.jitter_ms if jitter is None else jitter,
        )

    payload = {
        "bandwidth_bps": settings.bandwidth_bps,
        "latency_ms": settings.latency_ms,
        "loss_pct": settings.loss_pct,
        "jitter_ms": settings.jitter_ms,
        "profile": settings.profile if profile is not None else state.profile,
    }
    return _post_json(state.control_port, "/configure", payload)


def get_status(*, state_path: Path = DEFAULT_STATE_PATH) -> dict[str, Any]:
    state = read_state(state_path)
    if state is None:
        return {"active": False}
    status = _get_json(state.control_port, "/status")
    status["active"] = True
    status["pid"] = state.pid
    if status.get("running_for_seconds", 0) > 10 and status.get("requests_handled", 0) == 0:
        status["warning"] = "No traffic detected. The app may not be using the proxy."
    return status


def stop_session(*, state_path: Path = DEFAULT_STATE_PATH) -> None:
    state = read_state(state_path)
    if state is None:
        return
    try:
        _post_json(state.control_port, "/shutdown", {})
    finally:
        clear_state(state_path)


_STATE_LOCK = threading.Lock()


@contextlib.contextmanager
def _exclusive_state(path: Path) -> Generator[None, None, None]:
    """Hold an in-process threading lock + a cross-process lock file."""
    lock_path = path.with_suffix(".lock")
    with _STATE_LOCK:
        _acquire_lock_file(lock_path)
        try:
            yield
        finally:
            try:
                lock_path.unlink()
            except OSError:
                pass


def _acquire_lock_file(lock_path: Path, timeout: float = 5.0) -> None:
    """Spin-acquire an exclusive lock file, stealing a stale one after *timeout* seconds."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode())
            finally:
                os.close(fd)
            return
        except FileExistsError:
            if time.monotonic() >= deadline:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
            else:
                time.sleep(0.02)


def write_state(state: SessionState, path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with _exclusive_state(path):
        tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        _restrict_permissions(tmp)
        os.replace(tmp, path)


def read_state(path: Path = DEFAULT_STATE_PATH) -> SessionState | None:
    with _exclusive_state(path):
        if not path.exists():
            return None
        try:
            state = SessionState(**json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            return None
        if not _is_pid_alive(state.pid):
            try:
                path.unlink()
            except OSError:
                pass
            return None
        return state


def clear_state(path: Path = DEFAULT_STATE_PATH) -> None:
    with _exclusive_state(path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _restrict_permissions(path: Path) -> None:
    """Set owner-only read/write permissions (0o600) on a file.

    This is a best-effort call — it silently does nothing on platforms that
    don't support POSIX-style permissions (e.g. some Windows configurations).
    """
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):
        pass


def _proxy_env(env: dict[str, str], traffic_port: int) -> dict[str, str]:
    http_proxy_url = f"http://127.0.0.1:{traffic_port}"
    env.update(
        {
            "ALL_PROXY": http_proxy_url,
            "all_proxy": http_proxy_url,
            "HTTP_PROXY": http_proxy_url,
            "HTTPS_PROXY": http_proxy_url,
            "http_proxy": http_proxy_url,
            "https_proxy": http_proxy_url,
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "no_proxy": "localhost,127.0.0.1,::1",
        }
    )
    return env


def _is_pid_alive(pid: int) -> bool:
    try:
        psutil.Process(pid)
        return True
    except psutil.NoSuchProcess:
        return False


def _find_free_port(starting: int, *, attempts: int = 100) -> int:
    for port in range(starting, starting + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return int(sock.getsockname()[1])
    ending = starting + attempts - 1
    raise SessionError(f"no free TCP port found on 127.0.0.1 in range {starting}-{ending}")


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is None:
        process.terminate()


def _post_json(port: int, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        data = response.read()
        if response.status >= 400:
            raise SessionError(data.decode("utf-8") or f"HTTP {response.status}")
        return json.loads(data.decode("utf-8") or "{}")
    finally:
        conn.close()


def _patch_json(port: int, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("PATCH", path, body=body, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        data = response.read()
        if response.status >= 400:
            raise SessionError(data.decode("utf-8") or f"HTTP {response.status}")
        return json.loads(data.decode("utf-8") or "{}")
    finally:
        conn.close()


def _get_json(port: int, path: str) -> dict[str, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        data = response.read()
        if response.status >= 400:
            raise SessionError(data.decode("utf-8") or f"HTTP {response.status}")
        return json.loads(data.decode("utf-8") or "{}")
    finally:
        conn.close()


def add_rule(
    *,
    pattern: str,
    bandwidth: str | int | float | None = None,
    latency: str | int | float | None = None,
    loss: str | int | float | None = None,
    jitter: str | int | float | None = None,
    comment: str = "",
    state_path: Path = DEFAULT_STATE_PATH,
) -> dict[str, Any]:
    """Add a per-endpoint throttle rule to the running session."""
    state = read_state(state_path)
    if state is None:
        raise SessionError("no active NetShape session")
    payload: dict[str, Any] = {"pattern": pattern, "comment": comment}
    if bandwidth is not None:
        payload["bandwidth_bps"] = parse_bandwidth(bandwidth)
    if latency is not None:
        payload["latency_ms"] = parse_latency(latency)
    if jitter is not None:
        payload["jitter_ms"] = parse_duration_ms(jitter, kind="jitter")
    if loss is not None:
        payload["loss_pct"] = parse_loss(loss)
    result = _post_json(state.control_port, "/rules", payload)
    # Persist so rules survive across sessions.
    try:
        _save_persisted_rules(_get_json(state.control_port, "/rules").get("rules", []))
    except Exception:  # nosec B110 – persistence failure must not block the add_rule return
        pass
    return result


def remove_rule(rule_id: str, *, state_path: Path = DEFAULT_STATE_PATH) -> dict[str, Any]:
    """Remove a rule by id prefix OR by comment/name from the running session."""
    state = read_state(state_path)
    if state is None:
        raise SessionError("no active NetShape session")

    # Resolve comment/name → id if the caller passed a name rather than an id.
    resolved = rule_id
    try:
        rules = _get_json(state.control_port, "/rules").get("rules", [])
        # Exact comment match (case-insensitive) takes priority over id prefix.
        for r in rules:
            if r.get("comment", "").lower() == rule_id.lower():
                resolved = r["id"]
                break
    except Exception:  # nosec B110 – fallback to raw id if name-resolution fails
        pass

    conn = __import__("http.client", fromlist=["HTTPConnection"]).HTTPConnection(
        "127.0.0.1", state.control_port, timeout=5
    )
    try:
        conn.request("DELETE", f"/rules/{resolved}")
        response = conn.getresponse()
        data = response.read()
        if response.status >= 400:
            raise SessionError(data.decode("utf-8") or f"HTTP {response.status}")
        result = json.loads(data.decode("utf-8") or "{}")
    finally:
        conn.close()

    # Persist updated list.
    try:
        _save_persisted_rules(_get_json(state.control_port, "/rules").get("rules", []))
    except Exception:  # nosec B110 – persistence failure must not block the remove_rule return
        pass
    return result


def toggle_rule(rule_id: str, enabled: bool, *, state_path: Path = DEFAULT_STATE_PATH) -> dict[str, Any]:
    """Enable or disable a rule by id prefix or comment/name."""
    state = read_state(state_path)
    if state is None:
        raise SessionError("no active NetShape session")

    # Resolve comment/name → id (same logic as remove_rule)
    resolved = rule_id
    try:
        rules = _get_json(state.control_port, "/rules").get("rules", [])
        for r in rules:
            if r.get("comment", "").lower() == rule_id.lower():
                resolved = r["id"]
                break
    except Exception:  # nosec B110 – fallback to raw id if name-resolution fails
        pass

    return _patch_json(state.control_port, f"/rules/{resolved}", {"enabled": enabled})


def list_rules(*, state_path: Path = DEFAULT_STATE_PATH) -> list[dict[str, Any]]:
    """Return the active rules from the running session."""
    state = read_state(state_path)
    if state is None:
        raise SessionError("no active NetShape session")
    result = _get_json(state.control_port, "/rules")
    return result.get("rules", [])


def start_scenario_on_session(
    scenario_dict: dict[str, Any],
    *,
    state_path: Path = DEFAULT_STATE_PATH,
) -> dict[str, Any]:
    """Send a scenario to the running proxy. Returns initial scenario state."""
    state = read_state(state_path)
    if state is None:
        raise SessionError("no active NetShape session")
    return _post_json(state.control_port, "/scenario/start", scenario_dict)


def stop_scenario_on_session(*, state_path: Path = DEFAULT_STATE_PATH) -> dict[str, Any]:
    """Signal the running scenario to stop."""
    state = read_state(state_path)
    if state is None:
        raise SessionError("no active NetShape session")
    return _post_json(state.control_port, "/scenario/stop", {})


def get_scenario_status(*, state_path: Path = DEFAULT_STATE_PATH) -> dict[str, Any]:
    """Return the current scenario execution status from the running proxy."""
    state = read_state(state_path)
    if state is None:
        raise SessionError("no active NetShape session")
    return _get_json(state.control_port, "/scenario/status")


def get_metrics(*, state_path: Path = DEFAULT_STATE_PATH) -> dict[str, Any]:
    """Fetch proxy metrics as JSON."""
    state = read_state(state_path)
    if state is None:
        raise SessionError("no active NetShape session")
    return _get_json(state.control_port, "/metrics?format=json")


def get_metrics_prometheus(*, state_path: Path = DEFAULT_STATE_PATH) -> str:
    """Fetch proxy metrics in Prometheus text format."""
    state = read_state(state_path)
    if state is None:
        raise SessionError("no active NetShape session")
    conn = http.client.HTTPConnection("127.0.0.1", state.control_port, timeout=5)
    try:
        conn.request("GET", "/metrics")
        response = conn.getresponse()
        data = response.read()
        if response.status >= 400:
            raise SessionError(data.decode("utf-8") or f"HTTP {response.status}")
        return data.decode("utf-8")
    finally:
        conn.close()


def python_command(code: str) -> list[str]:
    """Small helper for tests and examples that need the current interpreter."""

    return [sys.executable, "-c", code]
