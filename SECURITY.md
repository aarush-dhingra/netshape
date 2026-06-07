# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 1.x | ✅ Yes |
| < 1.0 | ❌ No |

## Reporting a Vulnerability

**Please do not report security vulnerabilities via GitHub Issues.**

Email **aarushdhingra27@gmail.com** with:
- A description of the vulnerability
- Steps to reproduce
- The potential impact
- Any suggested mitigations (optional)

You should receive an acknowledgement within **48 hours** and a fix or mitigation plan within **7 days** for confirmed issues.

---

## Security Model

NetShape is a **local developer tool**. Understanding its security model helps set correct expectations:

### What is protected

- **Proxy binds to loopback only.** Both the traffic proxy (`127.0.0.1:8090`) and the control API (`127.0.0.1:8091`) listen exclusively on `127.0.0.1`. They are not reachable from the network.
- **CSRF protection.** The control API rejects `POST`/`PATCH`/`DELETE` requests that carry a non-local `Origin` header. This blocks DNS-rebinding and CSRF attacks from browser tabs.
- **Body size limit.** The control API rejects request bodies larger than 1 MB to prevent memory exhaustion.
- **Header injection prevention.** CRLF characters are stripped from forwarded HTTP header values.
- **Path traversal prevention.** User scenario names are sanitized and resolved paths are validated to stay inside `~/.netshape/scenarios/`.
- **File permissions.** State and config files in `~/.netshape/` are written with `0600` (owner read/write only) on POSIX systems.
- **No telemetry.** NetShape never makes any network request on your behalf except when you explicitly run `netshape test`.
- **SRI on CDN assets.** The dashboard's Chart.js dependency is pinned to a specific version with a Subresource Integrity hash.

### What is intentionally not protected

- **The control API is unauthenticated.** Any process running as the same user can call it. This is by design — the tool is single-user and the control API is how the CLI (`netshape adjust`, `netshape rule`, etc.) talks to the proxy. If you need isolation between processes, run separate instances.
- **The proxy is a forward proxy, not a MITM proxy.** NetShape does not decrypt HTTPS traffic. It establishes a transparent tunnel via `CONNECT`. TLS certificate validation happens between your app and the real server — NetShape never sees plaintext HTTPS content.
- **`shell=True` on Windows.** NetShape uses `subprocess.Popen(..., shell=True)` on Windows to correctly resolve `.cmd` script wrappers (e.g. `npx`). The command comes directly from the user's own CLI invocation.

### Known limitations

- **ReDoS via rule patterns.** Per-endpoint rule patterns are user-supplied regular expressions. A pathological regex (e.g. `(a+)+b`) can cause catastrophic backtracking. Avoid overly complex patterns; simple hostname patterns like `stripe\.com` are always safe.
- **State file world-readable on Windows.** `chmod(0o600)` has no effect on Windows NTFS unless the filesystem is configured for POSIX ACLs. The state file contains only port numbers and PID — no credentials.
