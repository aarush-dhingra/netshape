# NetShape

**Network throttling in one command.**

NetShape is a CLI tool and Python library that simplifies network throttling for developers and QA testers. Simulate 3G, 4G, slow WiFi, or any custom network condition with a single command.

## Install

```bash
pip install netshape
```

## Quick Start

```bash
# Throttle to 3G speeds (requires admin/root)
netshape start --profile 3g

# Check status
netshape status

# Verify throttling is working
netshape test

# Stop throttling
netshape stop

# List all available profiles
netshape profiles
```

## Built-in Profiles

| Profile | Bandwidth | Latency | Loss | Description |
|---------|-----------|---------|------|-------------|
| `3g` | 400 Kbps | 200ms | 1% | Standard 3G mobile |
| `3g-fast` | 1.2 Mbps | 150ms | 0.5% | Good 3G / HSPA |
| `3g-slow` | 240 Kbps | 400ms | 2% | Degraded 3G |
| `4g` | 32 Mbps | 50ms | 0% | Standard 4G/LTE |
| `lte` | 96 Mbps | 30ms | 0% | Fast LTE |
| `edge` | 96 Kbps | 500ms | 2% | EDGE (2.5G) |
| `2g` | 40 Kbps | 800ms | 5% | GPRS/2G |
| `slow-wifi` | 8 Mbps | 80ms | 3% | Coffee shop WiFi |
| `flaky-wifi` | 16 Mbps | 100ms | 10% | Unreliable WiFi |
| `satellite` | 40 Mbps | 600ms | 1% | Satellite internet |
| `dial-up` | 48 Kbps | 1000ms | 5% | 56k modem |
| `offline` | 0 | - | 100% | Complete failure |

## Custom Values

```bash
netshape start --bandwidth 250kbps --latency 300ms --loss 2%

# Override a single value from a preset
netshape start --profile 3g --bandwidth 100kbps
```

## Custom Profiles

```bash
# Save
netshape profile save "mumbai-4g" --bandwidth 800kbps --latency 120ms --loss 1%

# Use
netshape start --profile mumbai-4g

# Export/import
netshape profile export mumbai-4g --output mumbai-4g.json
netshape profile import mumbai-4g.json

# Delete
netshape profile delete mumbai-4g
```

## Python API

```python
import netshape

# Context manager (recommended)
with netshape.throttle(profile="3g"):
    response = requests.get("https://api.example.com/data")
    assert response.elapsed.total_seconds() > 1

# Manual start/stop
netshape.start(profile="3g")
netshape.stop()

# Speed test
result = netshape.speed_test()
print(result.download_speed_bps)
print(result.latency_ms)

# Status
state = netshape.status()

# Profile management
netshape.profiles.list()
netshape.profiles.save("custom", bandwidth="500kbps", latency="100ms", loss="1%")
```

## Useful Flags

```bash
# Preview commands without executing
netshape start --profile 3g --dry-run

# Detailed output
netshape start --profile 3g --verbose

# Override active session
netshape start --profile 4g --force

# Specify network interface
netshape start --profile 3g --interface "Wi-Fi"
```

## Crash Recovery

If your terminal crashes while throttling is active:

```bash
netshape cleanup
```

This removes any lingering OS-level network rules. Safe to run anytime.

> **Important:** Always run `netshape cleanup` before uninstalling the package.

## Platform Support

| Platform | Status |
|----------|--------|
| Windows | Bandwidth throttling via QoS policies |
| macOS | Planned (pfctl/dnctl) |
| Linux | Planned (tc/netem) |

## Requirements

- Python 3.10+
- Admin/root privileges for `start`, `stop`, `cleanup`
- No admin needed for `status`, `profiles`, `test`

## License

MIT
