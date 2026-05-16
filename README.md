# NetShape

NetShape runs desktop apps through a local throttling proxy so developers can test degraded network conditions without OS-level network rules or administrator privileges.

This repository is being rebuilt incrementally from the proxy-based architecture plan.

## Development

```bash
python -m pip install -e ".[dev]"
python -m netshape --version
```

## Usage

List the built-in network profiles:

```bash
netshape profiles
```

Run an app through the local proxy:

```bash
netshape run --profile 3g -- python -c "print('hello')"
```

While a session is active, use another terminal to inspect or adjust it:

```bash
netshape status
netshape adjust --latency 500ms
netshape stop
```

Automatically stop a long-running session:

```bash
netshape run --profile 3g --timeout 30m -- your-app-command
```

Verify that the proxy can handle traffic:

```bash
netshape test --profile 3g
```

NetShape uses one shared bidirectional bandwidth bucket for each proxy session, so upload and download traffic compete for the configured capacity like a constrained network link.

## Testing

Run the automated suite:

```bash
python -m pytest
```

Quick CLI smoke checks:

```bash
python -m netshape --version
python -m netshape status --json
python -m netshape run --port 0 --profile 3g -- python -c "print('ok')"
python -m netshape test --profile 3g --bytes 1024
```

Manual proxy smoke test with real HTTP traffic:

```bash
netshape run --profile 3g -- curl http://example.com
```

For desktop apps, launch the app command after `--`; Electron and most CLI tools inherit `HTTP_PROXY` and `HTTPS_PROXY` from the NetShape session.
