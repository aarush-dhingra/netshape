"""JSONL logging for NetShape operations."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAX_LINES = 1000


class NetShapeLogger:
    """Append-only JSONL logger with simple line-count rotation."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (Path.home() / ".netshape")
        self.log_dir = self.base_dir / "logs"
        self.log_path = self.log_dir / "netshape.log"

    def _ensure_dir(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **kwargs: Any) -> None:
        """Append a log entry. Silent on failure — logging must never crash the tool."""
        try:
            self._ensure_dir()
            entry: dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "pid": os.getpid(),
                **kwargs,
            }
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

            self._rotate_if_needed()
        except OSError:
            pass

    def _rotate_if_needed(self) -> None:
        """Keep only the last _MAX_LINES lines."""
        try:
            if not self.log_path.exists():
                return
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > _MAX_LINES:
                with open(self.log_path, "w", encoding="utf-8") as f:
                    f.writelines(lines[-_MAX_LINES:])
        except OSError:
            pass
