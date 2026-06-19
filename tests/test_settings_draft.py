#!/usr/bin/env python3
"""Tests for settings draft vs saved snapshot helpers."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace

sys.path.insert(0, ".")
from android_tv_connect.config import AppConfig, CaptureConfig, default_config
from android_tv_connect.settings_draft import (
    capture_stream_changed,
    config_snapshot,
    configs_differ,
    configs_equal,
)


class SettingsDraftTests(unittest.TestCase):
    def test_config_snapshot_is_independent_copy(self) -> None:
        original = default_config()
        snap = config_snapshot(original)
        self.assertTrue(configs_equal(original, snap))
        mutated = replace(snap, adb=replace(snap.adb, wired_serial="ABC123"))
        self.assertNotEqual(original.adb.wired_serial, "ABC123")
        self.assertEqual(mutated.adb.wired_serial, "ABC123")

    def test_configs_equal_detects_nested_change(self) -> None:
        a = default_config()
        b = replace(a, capture=replace(a.capture, framerate=60))
        self.assertFalse(configs_equal(a, b))
        self.assertTrue(configs_differ(a, b))

    def test_cancel_restore_snapshot_logic(self) -> None:
        """Saved snapshot stays unchanged while a live draft mutates."""
        saved = config_snapshot(default_config())
        draft = replace(
            saved,
            input=replace(saved.input, click_to_control=False),
            window=replace(saved.window, chrome_auto_hide=False),
        )
        self.assertTrue(configs_differ(draft, saved))
        restored = config_snapshot(saved)
        self.assertTrue(configs_equal(restored, saved))
        self.assertFalse(configs_equal(draft, restored))

    def test_capture_stream_changed(self) -> None:
        before = CaptureConfig(width=1920, height=1080, framerate=30)
        after = replace(before, framerate=60)
        self.assertTrue(capture_stream_changed(before, after))
        self.assertFalse(capture_stream_changed(before, before))


if __name__ == "__main__":
    unittest.main(verbosity=2)
