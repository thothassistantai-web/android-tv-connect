#!/usr/bin/env python3
"""Tests for ADB device discovery helpers."""

from __future__ import annotations

import ipaddress
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from android_tv_connect.adb_discovery import (
    discover_adb_devices,
    parse_adb_devices_l,
    scan_subnet_for_wireless_adb,
)


class AdbDiscoveryTests(unittest.TestCase):
    def test_parse_usb_and_wireless_devices(self) -> None:
        stdout = (
            "List of devices attached\n"
            "192.168.1.10:5555 device product:tv model:shield\n"
            "ABC123456789 device usb:1-2 product:shield\n"
            "offline-serial offline transport_id:9\n"
        )
        wired, wireless = parse_adb_devices_l(stdout)
        self.assertEqual(len(wired), 1)
        self.assertEqual(wired[0].serial, "ABC123456789")
        self.assertIn("usb:1-2", wired[0].description)
        self.assertEqual(len(wireless), 1)
        self.assertEqual(wireless[0].host, "192.168.1.10")
        self.assertEqual(wireless[0].port, 5555)

    def test_discover_uses_adb_devices_output(self) -> None:
        def fake_run(args):
            if args[:1] == ["start-server"]:
                return MagicMock(returncode=0, stdout="", stderr="")
            if args[:1] == ["devices"]:
                return MagicMock(
                    returncode=0,
                    stdout=(
                        "List of devices attached\n"
                        "USB999 device usb:2-1 product:stick\n"
                        "10.0.0.5:5555 device product:tv\n"
                    ),
                    stderr="",
                )
            raise AssertionError(f"unexpected adb args: {args}")

        wired, wireless = discover_adb_devices(run_adb=fake_run, scan_subnet=False)
        self.assertEqual([item.serial for item in wired], ["USB999"])
        self.assertEqual(wireless[0].address, "10.0.0.5:5555")

    def test_scan_subnet_collects_connectable_hosts(self) -> None:
        def fake_run(args):
            if args[:1] == ["connect"]:
                host = args[1]
                if host == "192.168.1.50:5555":
                    return MagicMock(returncode=0, stdout="connected to 192.168.1.50:5555\n", stderr="")
                return MagicMock(returncode=1, stdout="", stderr="failed")
            if args[:3] == ["-s", "192.168.1.50:5555", "get-state"]:
                return MagicMock(returncode=0, stdout="device\n", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")

        networks = [ipaddress.ip_network("192.168.1.50/32")]
        with patch("android_tv_connect.adb_discovery._local_ipv4_networks", return_value=networks):
            found = scan_subnet_for_wireless_adb(run_adb=fake_run)
        self.assertEqual([item.address for item in found], ["192.168.1.50:5555"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
