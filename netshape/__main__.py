"""Command entry point for NetShape."""

from __future__ import annotations

import argparse

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netshape",
        description="Run apps through a local throttling proxy.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"netshape {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
