from __future__ import annotations

from netshape.speed_test import run_speed_test


def test_run_speed_test_detects_proxy_traffic() -> None:
    result = run_speed_test(profile=None, byte_count=1024)

    assert result.bytes_downloaded == 1024
    assert result.requests_handled == 1
    assert result.proxy_detected is True
    assert result.direct_seconds >= 0
    assert result.proxied_seconds >= 0
