#!/usr/bin/env python3
"""Tests for seamless UX helpers, defaults migration, and hot-plug heuristics."""

from __future__ import annotations

import sys
import unittest

sys.path.insert(0, ".")
from android_tv_connect.adb_settings import (
    migrate_legacy_adb_defaults,
    normalize_adb_config,
    normalize_wireless_host,
)
from android_tv_connect.config import AdbConfig, default_config
from android_tv_connect.connection_ui import (
    LEGACY_WIRED_SERIAL,
    LEGACY_WIRELESS_HOST,
    capture_adb_mismatch_warning,
    detect_hotplug_switch,
    format_adb_chip_label,
)
from android_tv_connect.settings_store import _merge_dataclass


class DefaultsMigrationTests(unittest.TestCase):
    def test_fresh_defaults_are_neutral(self) -> None:
        cfg = default_config()
        self.assertEqual(cfg.adb.wired_serial, "")
        self.assertEqual(cfg.adb.wireless_host, "")

    def test_legacy_defaults_migrate_to_auto(self) -> None:
        adb = AdbConfig(
            wired_serial=LEGACY_WIRED_SERIAL,
            wireless_host=LEGACY_WIRELESS_HOST,
        )
        migrated = migrate_legacy_adb_defaults(adb)
        self.assertEqual(migrated.wired_serial, "")
        self.assertEqual(migrated.wireless_host, "")

    def test_custom_serial_preserved_on_migration(self) -> None:
        adb = AdbConfig(wired_serial="MYSTICK123", wireless_host="10.0.0.5")
        migrated = migrate_legacy_adb_defaults(adb)
        self.assertEqual(migrated.wired_serial, "MYSTICK123")
        self.assertEqual(migrated.wireless_host, "10.0.0.5")

    def test_load_merge_normalizes_legacy_values(self) -> None:
        raw = {
            "wired_serial": LEGACY_WIRED_SERIAL,
            "wireless_host": LEGACY_WIRELESS_HOST,
            "wireless_port": 5555,
        }
        adb = normalize_adb_config(_merge_dataclass(AdbConfig, raw))
        self.assertEqual(adb.wired_serial, "")
        self.assertEqual(adb.wireless_host, "")

    def test_empty_wireless_stays_empty(self) -> None:
        self.assertEqual(normalize_wireless_host(""), "")
        self.assertEqual(normalize_wireless_host("auto"), "")


class ConnectionUiTests(unittest.TestCase):
    def test_adb_chip_label_usb(self) -> None:
        label = format_adb_chip_label(
            connected=True,
            serial="ABC123456789",
            is_wireless=False,
        )
        self.assertIn("USB", label)
        self.assertIn("ABC123456789", label)

    def test_mismatch_when_wireless_with_usb_present(self) -> None:
        msg = capture_adb_mismatch_warning(
            capture_usb_present=True,
            adb_connected=True,
            adb_serial="192.168.1.10:5555",
            adb_is_wireless=True,
            usb_serials=["STICK_A", "STICK_B"],
            wireless_count=1,
        )
        self.assertIsNotNone(msg)
        self.assertIn("wireless", msg.lower())

    def test_no_mismatch_single_device(self) -> None:
        msg = capture_adb_mismatch_warning(
            capture_usb_present=True,
            adb_connected=True,
            adb_serial="STICK_A",
            adb_is_wireless=False,
            usb_serials=["STICK_A"],
            wireless_count=0,
        )
        self.assertIsNone(msg)


class HotplugDetectionTests(unittest.TestCase):
    def test_offers_switch_when_one_new_usb(self) -> None:
        serial = detect_hotplug_switch(
            previous_usb={"OLD_SERIAL"},
            current_usb={"NEW_SERIAL"},
            watch_serial="OLD_SERIAL",
            dismissed=set(),
        )
        self.assertEqual(serial, "NEW_SERIAL")

    def test_no_offer_when_multiple_new_devices(self) -> None:
        serial = detect_hotplug_switch(
            previous_usb={"OLD_SERIAL"},
            current_usb={"NEW_A", "NEW_B"},
            watch_serial="OLD_SERIAL",
            dismissed=set(),
        )
        self.assertIsNone(serial)

    def test_dismissed_serial_not_offered(self) -> None:
        serial = detect_hotplug_switch(
            previous_usb={"OLD_SERIAL"},
            current_usb={"NEW_SERIAL"},
            watch_serial="OLD_SERIAL",
            dismissed={"NEW_SERIAL"},
        )
        self.assertIsNone(serial)

    def test_no_offer_when_watch_still_present(self) -> None:
        serial = detect_hotplug_switch(
            previous_usb={"OLD_SERIAL"},
            current_usb={"OLD_SERIAL", "NEW_SERIAL"},
            watch_serial="OLD_SERIAL",
            dismissed=set(),
        )
        self.assertIsNone(serial)


if __name__ == "__main__":
    unittest.main(verbosity=2)
