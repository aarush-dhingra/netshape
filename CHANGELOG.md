# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.2] — Unreleased

### Security
- Added CSRF / DNS-rebinding protection: control API now rejects `POST`/`PATCH`/`DELETE` requests from non-local `Origin` headers.
- Added 1 MB body size limit on the control API to prevent memory exhaustion.
- Stripped CRLF characters from forwarded HTTP header values to prevent header injection.
- Validated user scenario paths against `~/.netshape/scenarios/` to prevent path traversal.
- State and config files (`~/.netshape/state.json`, `config.json`) are now written with `0600` permissions on POSIX systems.

### Fixed
- `ALL_PROXY` is now set to an `http://` URL instead of `socks5://`, resolving `socksio` dependency errors from libraries like LiteLLM.
- Dashboard profile selector now correctly updates live status and sliders when a profile is applied.
- Dashboard `<select>` elements now respect `color-scheme: dark`.

### Added
- `GET /profiles` control API endpoint for dynamic dashboard profile population.
- CI workflow (`.github/workflows/test.yml`) — runs pytest on Ubuntu, macOS, Windows across Python 3.10–3.13.
- `LICENSE` file (MIT).
- `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`.

### Changed
- Chart.js pinned to `4.4.7` with SRI hash (previously unpinned CDN latest).
- Google Fonts removed from dashboard; now uses system font stack.
- Author and project URLs in `pyproject.toml` updated to correct GitHub repository.

---

## [1.0.1] — 2026-05-10

### Fixed
- Corrected `pyproject.toml` metadata for successful PyPI publish.

---

## [1.0.0] — 2026-05-09

### Added
- Initial open-source release.
- `netshape run <cmd>` — start a child process with proxy environment injected.
- `netshape adjust` — live throttle adjustment.
- `netshape status` — display current proxy state.
- `netshape rule add/remove/list/enable/disable` — per-endpoint throttle rules.
- `netshape scenario run/stop/status/list` — scenario scripting.
- `netshape metrics` — connection and byte statistics.
- `netshape test` — proxy self-test.
- `netshape profiles` — list built-in network profiles.
- `netshape setup` — one-time CA setup helper.
- Web dashboard at `http://127.0.0.1:<port>/dashboard`.
- Built-in profiles: 2G, 3G, 4G LTE, Broadband, Cable, Fiber, WiFi, Satellite, Throttled.
- Persistent rules (`~/.netshape/rules.json`), config (`~/.netshape/config.json`).
