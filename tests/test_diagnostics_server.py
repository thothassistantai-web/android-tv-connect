"""Tests for diagnostics IPC protocol parsing and dispatch."""

from __future__ import annotations

import json
import logging
import sys
import unittest
from typing import Any
from unittest.mock import patch

sys.path.insert(0, ".")
from android_tv_connect.diagnostics_server import (
    RingBufferLogHandler,
    dispatch_command,
    format_response,
    handle_request_line,
    install_diagnostics_logging,
    parse_request,
)


class _FakeBackend:
    def diagnostics_status(self) -> dict[str, Any]:
        return {"app_running": True, "version": "9.9.9"}

    def diagnostics_enumerate(self) -> dict[str, Any]:
        return {"audio_sources": [], "video_devices": []}

    def diagnostics_audio_play(self, source: str) -> dict[str, Any]:
        return {"playing": True, "source": source, "app_pid": 1234}

    def diagnostics_audio_stop(self) -> dict[str, Any]:
        return {"playing": False, "source": None, "app_pid": 1234}

    def diagnostics_audio_test_next(self) -> dict[str, Any]:
        return {"playing": True, "source": "next.source", "app_pid": 1234}

    def diagnostics_capture_restart(self) -> dict[str, Any]:
        return {"capture_state": "reconnecting"}


class DiagnosticsProtocolTests(unittest.TestCase):
    def test_parse_request_requires_command(self) -> None:
        with self.assertRaises(ValueError):
            parse_request("{}")

    def test_parse_request_accepts_args(self) -> None:
        payload = parse_request('{"command": "logs", "args": {"lines": 20}}')
        self.assertEqual(payload["command"], "logs")
        self.assertEqual(payload["args"]["lines"], 20)

    def test_format_response_ok(self) -> None:
        line = format_response(ok=True, data={"pong": True})
        parsed = json.loads(line)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["pong"], True)

    def test_format_response_error(self) -> None:
        line = format_response(ok=False, error="nope")
        parsed = json.loads(line)
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"], "nope")

    def test_handle_request_ping(self) -> None:
        line = handle_request_line('{"command": "ping"}', backend=None)
        parsed = json.loads(line)
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["pong"])

    def test_handle_request_unknown_command(self) -> None:
        line = handle_request_line('{"command": "explode"}', backend=_FakeBackend())
        parsed = json.loads(line)
        self.assertFalse(parsed["ok"])
        self.assertIn("unknown command", parsed["error"])

    def test_dispatch_status_with_backend(self) -> None:
        data = dispatch_command(_FakeBackend(), "status", {})
        self.assertEqual(data["version"], "9.9.9")

    def test_dispatch_audio_play_passes_source(self) -> None:
        data = dispatch_command(_FakeBackend(), "audio-play", {"source": "alsa_input.test"})
        self.assertEqual(data["source"], "alsa_input.test")

    def test_dispatch_requires_backend_for_status(self) -> None:
        with self.assertRaises(RuntimeError):
            dispatch_command(None, "status", {})

    def test_dispatch_enumerate_without_backend(self) -> None:
        with patch(
            "android_tv_connect.diagnostics_server.enumerate_audio_sources",
            return_value=[],
        ), patch(
            "android_tv_connect.diagnostics_server.enumerate_v4l2_devices",
            return_value=[],
        ), patch(
            "android_tv_connect.diagnostics_server.is_ui_running",
            return_value=False,
        ):
            data = dispatch_command(None, "enumerate", {})
        self.assertEqual(data["audio_sources"], [])
        self.assertFalse(data["app_running"])

    def test_ring_buffer_log_handler_tail(self) -> None:
        handler = RingBufferLogHandler(capacity=3)
        handler.setFormatter(logging.Formatter("%(message)s"))
        for index in range(4):
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg=f"line-{index}",
                args=(),
                exc_info=None,
            )
            handler.emit(record)
        self.assertEqual(handler.tail(2), ["line-2", "line-3"])

    def test_install_diagnostics_logging_idempotent(self) -> None:
        first = install_diagnostics_logging()
        second = install_diagnostics_logging()
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
