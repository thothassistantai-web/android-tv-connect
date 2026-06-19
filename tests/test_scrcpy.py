#!/usr/bin/env python3
"""Tests for scrcpy integration (argv building, availability, target resolution)."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from android_tv_connect.config import AppConfig, ScrcpyConfig
from android_tv_connect.scrcpy_manager import (
    build_scrcpy_argv,
    is_scrcpy_available,
    resolve_scrcpy_command,
    resolve_scrcpy_target,
)


class ScrcpyArgvTests(unittest.TestCase):
    def test_build_wired_argv(self) -> None:
        scrcpy = ScrcpyConfig(
            max_size=1280,
            bit_rate="4M",
            no_audio=True,
            stay_awake=True,
            window_title="TV Mirror",
        )
        argv = build_scrcpy_argv(scrcpy, "ABC123USB")
        self.assertEqual(argv[0], "scrcpy")
        self.assertIn("--serial=ABC123USB", argv)
        self.assertIn("--max-size=1280", argv)
        self.assertIn("--video-bit-rate=4M", argv)
        self.assertIn("--no-audio", argv)
        self.assertIn("--stay-awake", argv)
        self.assertIn("--window-title=TV Mirror", argv)
        self.assertNotIn("--fullscreen", argv)

    def test_build_wireless_argv(self) -> None:
        scrcpy = ScrcpyConfig(fullscreen=True, turn_screen_off=True, no_audio=False)
        argv = build_scrcpy_argv(scrcpy, "192.168.1.50:5555")
        self.assertIn("--serial=192.168.1.50:5555", argv)
        self.assertIn("--fullscreen", argv)
        self.assertIn("--turn-screen-off", argv)
        self.assertNotIn("--no-audio", argv)

    def test_custom_scrcpy_path(self) -> None:
        scrcpy = ScrcpyConfig(scrcpy_path="/opt/bin/scrcpy")
        argv = build_scrcpy_argv(scrcpy, "SERIAL")
        self.assertEqual(argv[0], "/opt/bin/scrcpy")

    def test_max_size_zero_omits_flag(self) -> None:
        scrcpy = ScrcpyConfig(max_size=0)
        argv = build_scrcpy_argv(scrcpy, "SERIAL")
        self.assertFalse(any(arg.startswith("--max-size=") for arg in argv))


class ScrcpyAvailabilityTests(unittest.TestCase):
    def test_resolve_command_default(self) -> None:
        self.assertEqual(resolve_scrcpy_command(""), "scrcpy")
        self.assertEqual(resolve_scrcpy_command("  "), "scrcpy")

    @patch("android_tv_connect.scrcpy_manager.shutil.which", return_value="/usr/bin/scrcpy")
    def test_available_on_path(self, _which) -> None:
        self.assertTrue(is_scrcpy_available())

    @patch("android_tv_connect.scrcpy_manager.shutil.which", return_value=None)
    def test_not_available(self, _which) -> None:
        self.assertFalse(is_scrcpy_available())


class ScrcpyTargetTests(unittest.TestCase):
    def test_missing_scrcpy(self) -> None:
        config = AppConfig()
        adb = MagicMock()
        with patch(
            "android_tv_connect.scrcpy_manager.is_scrcpy_available",
            return_value=False,
        ):
            argv, error = resolve_scrcpy_target(config, adb)
        self.assertIsNone(argv)
        self.assertIn("scrcpy not found", error or "")

    def test_uses_active_adb_serial(self) -> None:
        config = AppConfig(
            scrcpy=ScrcpyConfig(max_size=1920, no_audio=True),
        )
        adb = MagicMock()
        adb.is_connected.return_value = True
        adb.active_serial.return_value = "FUSA2541006925"
        with patch(
            "android_tv_connect.scrcpy_manager.is_scrcpy_available",
            return_value=True,
        ):
            argv, error = resolve_scrcpy_target(config, adb)
        self.assertIsNone(error)
        assert argv is not None
        self.assertIn("--serial=FUSA2541006925", argv)
        adb.connect.assert_not_called()

    def test_connects_when_offline(self) -> None:
        config = AppConfig()
        adb = MagicMock()
        adb.is_connected.return_value = False
        adb.connect.return_value = True
        adb.active_serial.return_value = "192.168.1.157:5555"
        with patch(
            "android_tv_connect.scrcpy_manager.is_scrcpy_available",
            return_value=True,
        ):
            argv, error = resolve_scrcpy_target(config, adb)
        self.assertIsNone(error)
        assert argv is not None
        self.assertIn("--serial=192.168.1.157:5555", argv)
        adb.connect.assert_called_once()

    def test_connect_failure(self) -> None:
        config = AppConfig()
        adb = MagicMock()
        adb.is_connected.return_value = False
        adb.connect.return_value = False
        with patch(
            "android_tv_connect.scrcpy_manager.is_scrcpy_available",
            return_value=True,
        ):
            argv, error = resolve_scrcpy_target(config, adb)
        self.assertIsNone(argv)
        self.assertIn("ADB connection failed", error or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
