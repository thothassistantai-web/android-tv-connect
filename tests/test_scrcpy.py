#!/usr/bin/env python3
"""Tests for scrcpy integration (argv building, availability, target resolution)."""

from __future__ import annotations

import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from android_tv_connect.config import AppConfig, ScrcpyConfig
from android_tv_connect.scrcpy_manager import (
    ScrcpyCapabilities,
    ScrcpySession,
    build_scrcpy_argv,
    clear_scrcpy_capabilities_cache,
    format_scrcpy_exit_message,
    is_scrcpy_available,
    probe_scrcpy_capabilities,
    resolve_scrcpy_command,
    resolve_scrcpy_target,
)


class ScrcpyArgvTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_scrcpy_capabilities_cache()

    def test_build_wired_argv_scrcpy2(self) -> None:
        scrcpy = ScrcpyConfig(
            max_size=1280,
            bit_rate="4M",
            no_audio=True,
            stay_awake=True,
            window_title="TV Mirror",
        )
        caps = ScrcpyCapabilities(2, 4)
        argv = build_scrcpy_argv(scrcpy, "ABC123USB", capabilities=caps)
        self.assertEqual(argv[0], "scrcpy")
        self.assertIn("--serial=ABC123USB", argv)
        self.assertIn("--max-size=1280", argv)
        self.assertIn("--video-bit-rate=4M", argv)
        self.assertNotIn("--bit-rate=4M", argv)
        self.assertIn("--no-audio", argv)
        self.assertIn("--stay-awake", argv)
        self.assertIn("--window-title=TV Mirror", argv)
        self.assertNotIn("--fullscreen", argv)

    def test_build_argv_scrcpy1_uses_bit_rate(self) -> None:
        scrcpy = ScrcpyConfig(bit_rate="8M", no_audio=True)
        caps = ScrcpyCapabilities(1, 25)
        argv = build_scrcpy_argv(scrcpy, "SERIAL", capabilities=caps)
        self.assertIn("--bit-rate=8M", argv)
        self.assertNotIn("--video-bit-rate=8M", argv)
        self.assertNotIn("--no-audio", argv)

    def test_build_wireless_argv(self) -> None:
        scrcpy = ScrcpyConfig(fullscreen=True, turn_screen_off=True, no_audio=False)
        argv = build_scrcpy_argv(
            scrcpy,
            "192.168.1.50:5555",
            capabilities=ScrcpyCapabilities(2, 0),
        )
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

    @patch(
        "android_tv_connect.scrcpy_manager.subprocess.run",
        return_value=MagicMock(stdout="scrcpy 1.25\n", stderr="", returncode=0),
    )
    def test_probe_scrcpy_capabilities(self, _run) -> None:
        caps = probe_scrcpy_capabilities()
        assert caps is not None
        self.assertEqual(caps.major, 1)
        self.assertEqual(caps.minor, 25)
        self.assertFalse(caps.uses_video_bit_rate)
        self.assertFalse(caps.supports_no_audio)


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
        ), patch(
            "android_tv_connect.scrcpy_manager.build_scrcpy_argv",
            return_value=["scrcpy", "--serial=FUSA2541006925"],
        ) as build:
            argv, error = resolve_scrcpy_target(config, adb)
        self.assertIsNone(error)
        assert argv is not None
        build.assert_called_once()
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


class ScrcpySessionTests(unittest.TestCase):
    def test_quick_exit_invokes_callback_without_running_notify(self) -> None:
        events: list[bool] = []
        quick_exits: list[tuple[int, list[str]]] = []

        session = ScrcpySession(
            on_state_change=events.append,
            on_quick_exit=lambda code, lines: quick_exits.append((code, lines)),
            startup_grace_s=0.05,
            quick_exit_threshold_s=2.0,
        )

        class FakeStdout:
            def __iter__(self):
                return iter(())

        class FakeProc:
            stdout = FakeStdout()

            def poll(self) -> int | None:
                return 1

            def wait(self) -> int:
                return 1

        proc = FakeProc()
        session._started_at = time.monotonic()
        session._log_buffer = ["scrcpy: bad option"]
        session._drain_output(proc)  # type: ignore[arg-type]

        self.assertEqual(events, [])
        self.assertEqual(len(quick_exits), 1)
        self.assertEqual(quick_exits[0][0], 1)
        self.assertIn("bad option", quick_exits[0][1][0])

    def test_running_notify_after_startup_grace(self) -> None:
        events: list[bool] = []

        session = ScrcpySession(
            on_state_change=events.append,
            startup_grace_s=0.05,
            quick_exit_threshold_s=2.0,
        )

        class AliveProc:
            def poll(self) -> int | None:
                return None

        proc = AliveProc()
        session._watch_startup(proc)  # type: ignore[arg-type]
        self.assertEqual(events, [True])

    def test_format_scrcpy_exit_message(self) -> None:
        message = format_scrcpy_exit_message(1, ["line one", "line two"])
        self.assertIn("code 1", message)
        self.assertIn("line two", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
