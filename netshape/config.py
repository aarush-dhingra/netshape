"""User configuration management (~/.netshape/config.json)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _default_config() -> dict[str, Any]:
    return {
        "default_timeout_minutes": None,
        "speed_test_endpoint": None,
    }


class ConfigManager:
    """Read/write persistent user preferences from ~/.netshape/config.json."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (Path.home() / ".netshape")
        self.config_path = self.base_dir / "config.json"
        self._cache: dict[str, Any] | None = None

    def _ensure_dir(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        """Load config from disk, creating defaults if missing."""
        if self._cache is not None:
            return self._cache

        if self.config_path.exists():
            try:
                with open(self.config_path) as f:
                    self._cache = {**_default_config(), **json.load(f)}
            except (json.JSONDecodeError, OSError):
                self._cache = _default_config()
        else:
            self._cache = _default_config()

        return self._cache

    def save(self) -> None:
        """Write current config to disk."""
        self._ensure_dir()
        with open(self.config_path, "w") as f:
            json.dump(self._cache or _default_config(), f, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value."""
        return self.load().get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a config value and persist to disk."""
        config = self.load()
        config[key] = value
        self._cache = config
        self.save()
