#!/usr/bin/env python3
"""Tests for user-quit / watch auto-launch gating."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")
from android_tv_connect import __main__ as main_mod
from android_tv_connect.singleton import (
    clear_user_quit,
    is_user_quit_active,
    note_user_quit,
)


class UserQuitFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        cache = Path(self._tmpdir.name)
        self._quit_patch = patch.multiple(
            "android_tv_connect.singleton",
            CACHE_DIR=cache,
            LOCK_PATH=cache / "ui.lock",
            USER_QUIT_PATH=cache / "user-quit",
        )
        self._quit_patch.start()

    def tearDown(self) -> None:
        self._quit_patch.stop()
        self._tmpdir.cleanup()

    def test_note_and_clear_user_quit(self) -> None:
        self.assertFalse(is_user_quit_active())
        note_user_quit()
        self.assertTrue(is_user_quit_active())
        clear_user_quit()
        self.assertFalse(is_user_quit_active())

    def test_clear_user_quit_is_idempotent(self) -> None:
        clear_user_quit()
        clear_user_quit()


class ShouldAutoLaunchTests(unittest.TestCase):
    def test_launches_when_ready_and_not_running(self) -> None:
        self.assertTrue(
            main_mod.should_auto_launch_ui(
                devices_ready=True,
                ui_running=False,
                user_quit=False,
                seconds_since_last_launch=10.0,
            )
        )

    def test_skips_when_user_quit(self) -> None:
        self.assertFalse(
            main_mod.should_auto_launch_ui(
                devices_ready=True,
                ui_running=False,
                user_quit=True,
                seconds_since_last_launch=10.0,
            )
        )

    def test_skips_when_ui_running(self) -> None:
        self.assertFalse(
            main_mod.should_auto_launch_ui(
                devices_ready=True,
                ui_running=True,
                user_quit=False,
                seconds_since_last_launch=10.0,
            )
        )

    def test_skips_during_cooldown(self) -> None:
        self.assertFalse(
            main_mod.should_auto_launch_ui(
                devices_ready=True,
                ui_running=False,
                user_quit=False,
                seconds_since_last_launch=1.0,
            )
        )

    def test_skips_when_devices_not_ready(self) -> None:
        self.assertFalse(
            main_mod.should_auto_launch_ui(
                devices_ready=False,
                ui_running=False,
                user_quit=False,
                seconds_since_last_launch=10.0,
            )
        )


class RunQuitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        cache = Path(self._tmpdir.name)
        self._quit_patch = patch.multiple(
            "android_tv_connect.singleton",
            CACHE_DIR=cache,
            LOCK_PATH=cache / "ui.lock",
            USER_QUIT_PATH=cache / "user-quit",
        )
        self._quit_patch.start()

    def tearDown(self) -> None:
        self._quit_patch.stop()
        self._tmpdir.cleanup()

    @patch("android_tv_connect.__main__.stop_watch_service", return_value=True)
    def test_run_quit_sets_flag_and_stops_service(self, stop_mock) -> None:
        self.assertEqual(main_mod.run_quit(), 0)
        self.assertTrue(is_user_quit_active())
        stop_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
