#!/usr/bin/env python3
"""Verify ADB reconnect keeps trying after disconnect (FUSA reboot scenario)."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")
from android_tv_connect.adb_client import AdbClient


class AdbReconnectTests(unittest.TestCase):
    def test_health_poll_retries_after_serial_cleared(self) -> None:
        """Regression: failed reconnect cleared _serial and stopped all retries."""
        client = AdbClient(wired_serial="FUSA2541006925")
        client.connect = lambda: True  # type: ignore[method-assign]
        client._serial = "FUSA2541006925"
        client._start_health_poll()

        attempts: list[int] = []

        def fake_attempt() -> bool:
            attempts.append(1)
            client._serial = None
            return False

        with patch.object(AdbClient, "HEALTH_POLL_INTERVAL", 0.05), patch.object(
            AdbClient, "HEALTH_POLL_INTERVAL_DISCONNECTED", 0.05
        ), patch.object(AdbClient, "_RECONNECT_BASE_DELAY", 0.01), patch.object(
            AdbClient, "_RECONNECT_MAX_DELAY", 0.05
        ), patch.object(
            client, "_device_ready", return_value=False
        ), patch.object(
            client, "_attempt_reconnect", side_effect=fake_attempt
        ):
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and len(attempts) < 3:
                time.sleep(0.05)

        client._stop_health_poll()
        self.assertGreaterEqual(
            len(attempts),
            2,
            "health poll must keep calling _attempt_reconnect after _serial is cleared",
        )

    def test_connection_callback_on_reconnect(self) -> None:
        events: list[bool] = []
        client = AdbClient(on_connection_change=events.append)
        client._notify_connection_change(True)
        client._notify_connection_change(True)
        client._notify_connection_change(False)
        client._notify_connection_change(True)
        self.assertEqual(events, [True, False, True])


if __name__ == "__main__":
    unittest.main(verbosity=2)
