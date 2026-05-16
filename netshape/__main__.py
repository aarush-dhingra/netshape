"""Command entry point for ``python -m netshape``."""

from __future__ import annotations

from .cli import app


def main(argv: list[str] | None = None) -> int:
    app(args=argv, prog_name="netshape")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
