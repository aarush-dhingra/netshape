"""Command entry point for ``python -m netshape``."""

from __future__ import annotations

from .cli import app


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv, prog_name="netshape")
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
