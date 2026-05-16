"""Tests for netshape.speed_test — using mocks for network calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from netshape.speed_test import (
    format_speed,
    format_speed_bytes,
    measure_download_speed,
    measure_latency,
    run_speed_test,
)


class TestFormatSpeed:
    def test_megabits(self) -> None:
        assert format_speed(5_000_000) == "5.0 Mbps"

    def test_kilobits(self) -> None:
        assert format_speed(500_000) == "500.0 Kbps"

    def test_bits(self) -> None:
        assert format_speed(500) == "500 bps"

    def test_bytes_megabytes(self) -> None:
        assert format_speed_bytes(8_000_000) == "1.0 MB/s"

    def test_bytes_kilobytes(self) -> None:
        assert format_speed_bytes(800_000) == "100.0 KB/s"


class TestMeasureDownloadSpeed:
    @patch("netshape.speed_test.urllib.request.urlopen")
    def test_measures_speed(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = b"x" * 100_000
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        bps, endpoint = measure_download_speed("https://test.example.com/file")
        assert bps > 0
        assert endpoint == "https://test.example.com/file"

    @patch("netshape.speed_test.urllib.request.urlopen")
    def test_fallback_on_failure(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = b"x" * 50_000
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        # First call fails, second succeeds
        mock_urlopen.side_effect = [
            ConnectionError("fail"),
            mock_response,
        ]

        bps, endpoint = measure_download_speed()
        assert bps > 0

    @patch("netshape.speed_test.urllib.request.urlopen")
    def test_all_endpoints_fail(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = ConnectionError("fail")

        with pytest.raises(RuntimeError, match="cannot reach"):
            measure_download_speed()


class TestMeasureLatency:
    @patch("netshape.speed_test.socket.socket")
    def test_measures_latency(self, mock_socket_cls: MagicMock) -> None:
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        latency = measure_latency(attempts=1)
        assert latency >= 0
        mock_sock.connect.assert_called_once()
        mock_sock.close.assert_called_once()

    @patch("netshape.speed_test.socket.socket")
    def test_all_fail_returns_negative(self, mock_socket_cls: MagicMock) -> None:
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionError("fail")
        mock_socket_cls.return_value = mock_sock

        latency = measure_latency(attempts=3)
        assert latency == -1.0


class TestRunSpeedTest:
    @patch("netshape.speed_test.measure_packet_loss", return_value=0.0)
    @patch("netshape.speed_test.measure_latency", return_value=15.0)
    @patch("netshape.speed_test.measure_download_speed", return_value=(50_000_000.0, "https://test.com"))
    def test_returns_result(
        self, mock_speed: MagicMock, mock_latency: MagicMock, mock_loss: MagicMock,
    ) -> None:
        result = run_speed_test(active_profile="3g", is_throttled=True)
        assert result.download_speed_bps == 50_000_000.0
        assert result.latency_ms == 15.0
        assert result.packet_loss_pct == 0.0
        assert result.is_throttled is True
        assert result.profile_name == "3g"
