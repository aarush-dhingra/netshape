# Contributing to NetShape

Thank you for your interest in contributing! This document explains how to get started.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/aarush-dhingra/netshape.git
cd netshape

# Create a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Verify everything works
python -m netshape --version
pytest tests/ -v
```

## Project Structure

```
netshape/
├── netshape/
│   ├── cli.py           # Typer CLI — all commands
│   ├── core.py          # Session orchestration, state, env injection
│   ├── proxy_server.py  # asyncio proxy engine + control API
│   ├── throttle.py      # TokenBucket, latency, loss primitives
│   ├── profiles.py      # Built-in profile loading
│   ├── scenario.py      # YAML scenario parsing and execution
│   ├── units.py         # Bandwidth/latency/loss/jitter parsing
│   ├── speed_test.py    # Local proxy verification test
│   ├── dashboard/       # Web dashboard (HTML, CSS, JS)
│   └── data/            # Built-in profiles JSON + scenario YAML files
└── tests/               # pytest test suite
```

## Running Tests

```bash
# Full suite
pytest tests/ -v

# Specific file
pytest tests/test_proxy_server.py -v

# With coverage
pytest tests/ --cov=netshape --cov-report=term-missing
```

## Code Style

We use `ruff` for linting and formatting:

```bash
pip install ruff
ruff check netshape/ tests/
ruff format netshape/ tests/
```

## Security Checks

```bash
# Static analysis
bandit -r netshape/ -ll

# Dependency audit
pip-audit
```

## Submitting Changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes and add tests
4. Ensure `pytest` and `ruff check` pass
5. Open a pull request against `main`

Please include a clear description of the problem your PR solves and reference any related issues.

## Reporting Bugs

Open a GitHub Issue with:
- NetShape version (`netshape --version`)
- Python version and OS
- Exact command run and error output
- What you expected to happen

## Security Issues

See [SECURITY.md](SECURITY.md) — please do **not** open a public GitHub Issue for security vulnerabilities.
