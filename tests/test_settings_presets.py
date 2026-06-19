#!/usr/bin/env python3
"""Tests for settings preset parsing and validation."""

from __future__ import annotations

import sys
import unittest

sys.path.insert(0, ".")
from android_tv_connect.config import AdbConfig, CaptureConfig, ScrcpyConfig
from android_tv_connect.settings_presets import (
    RESOLUTION_PRESETS,
    bit_rate_index,
    framerate_index,
    max_size_index,
    normalize_capture_config,
    normalize_scrcpy_config,
    parse_bit_rate,
    parse_max_size,
    resolution_index,
    validate_adb_for_save,
    wireless_port_preset_index,
)


class SettingsPresetTests(unittest.TestCase):
    def test_resolution_index_native(self) -> None:
        self.assertEqual(resolution_index(0, 0), RESOLUTION_PRESETS.index(("Native", 0, 0)))

    def test_framerate_index_defaults_to_first_preset(self) -> None:
        self.assertEqual(framerate_index(99), 0)

    def test_bit_rate_presets(self) -> None:
        self.assertEqual(bit_rate_index("4M"), 1)
        rate, err = parse_bit_rate("8M")
        self.assertEqual(rate, "8M")
        self.assertIsNone(err)
        rate, err = parse_bit_rate("bad")
        self.assertIsNone(rate)
        self.assertIsNotNone(err)

    def test_max_size_zero_is_native(self) -> None:
        size, err = parse_max_size(0)
        self.assertEqual(size, 0)
        self.assertIsNone(err)
        self.assertEqual(max_size_index(0), 0)

    def test_wireless_port_preset_index_custom(self) -> None:
        self.assertEqual(wireless_port_preset_index(5555), 0)
        self.assertEqual(wireless_port_preset_index(7777), len([5555, 5037, 5556]))

    def test_normalize_capture_rejects_bad_video_device(self) -> None:
        cfg = normalize_capture_config(
            CaptureConfig(video_device="not-a-node", framerate=45)
        )
        self.assertEqual(cfg.video_device, "auto")
        self.assertEqual(cfg.framerate, 30)

    def test_normalize_scrcpy_config(self) -> None:
        cfg = normalize_scrcpy_config(ScrcpyConfig(bit_rate="4M", max_size=1080))
        self.assertEqual(cfg.bit_rate, "4M")
        self.assertEqual(cfg.max_size, 1080)

    def test_validate_adb_rejects_bad_host(self) -> None:
        adb, err = validate_adb_for_save(
            AdbConfig(wireless_host="bad host!", wireless_port=5555)
        )
        self.assertIsNone(adb)
        self.assertIsNotNone(err)

    def test_validate_adb_accepts_auto_host(self) -> None:
        adb, err = validate_adb_for_save(AdbConfig(wireless_host="", wireless_port=5555))
        self.assertIsNone(err)
        assert adb is not None
        self.assertEqual(adb.wireless_host, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
