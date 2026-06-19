#!/usr/bin/env python3
"""Tests for ADB settings normalization and auto-discovery."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from android_tv_connect.adb_client import AdbClient
from android_tv_connect.adb_settings import (
    normalize_adb_config,
    normalize_wired_serial,
    normalize_wireless_host,
    parse_wireless_port,
    wireless_host_is_auto,
)
from android_tv_connect.config import AdbConfig


class AdbSettingsTests(unittest.TestCase):
    def test_normalize_wired_serial_auto_values(self) -> None:
        self.assertEqual(normalize_wired_serial(""), "")
        self.assertEqual(normalize_wired_serial("auto"), "")
        self.assertEqual(normalize_wired_serial("AUTO"), "")
        self.assertEqual(normalize_wired_serial("*"), "")
        self.assertEqual(normalize_wired_serial("FUSA123"), "FUSA123")

    def test_parse_wireless_port(self) -> None:
        port, err = parse_wireless_port("")
        self.assertEqual(port, 5555)
        self.assertIsNone(err)
        port, err = parse_wireless_port("not-a-port")
        self.assertIsNone(port)
        self.assertIsNotNone(err)

    def test_load_normalizes_auto_serial(self) -> None:
        adb = normalize_adb_config(AdbConfig(wired_serial="auto"))
        self.assertEqual(adb.wired_serial, "")

    def test_normalize_wireless_host_auto(self) -> None:
        self.assertEqual(normalize_wireless_host("auto", default="1.2.3.4"), "")
        self.assertTrue(wireless_host_is_auto(""))
        self.assertTrue(wireless_host_is_auto("AUTO"))
        self.assertEqual(normalize_wireless_host("192.168.1.5", default="1.2.3.4"), "192.168.1.5")

    def test_auto_discover_usb_device(self) -> None:
        client = AdbClient(wired_serial="")
        fake = MagicMock(
            returncode=0,
            stdout=(
                "List of devices attached\n"
                "192.168.1.10:5555 device product:tv\n"
                "ABC123456789 device usb:1-2 product:shield\n"
            ),
        )
        with patch.object(client, "_run_adb", return_value=fake):
            self.assertEqual(client._find_wired_serial(), "ABC123456789")

    def test_prefer_wireless_tries_wireless_first(self) -> None:
        client = AdbClient(
            wired_serial="",
            wireless_host="192.168.1.50",
            wireless_port=5555,
            prefer_wired=False,
        )
        calls: list[list[str]] = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:1] == ["connect"]:
                return MagicMock(
                    returncode=0,
                    stdout="connected to 192.168.1.50:5555\n",
                    stderr="",
                )
            if args[:1] == ["devices"]:
                return MagicMock(returncode=0, stdout="List of devices attached\n", stderr="")
            if args[:3] == ["-s", "192.168.1.50:5555", "get-state"]:
                return MagicMock(returncode=0, stdout="device\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(client, "_run_adb", side_effect=fake_run), patch.object(
            client, "_start_health_poll"
        ), patch.object(client, "_set_connected"):
            self.assertTrue(client.connect())
        connect_calls = [args for args in calls if args[:1] == ["connect"]]
        self.assertEqual(connect_calls[0][:2], ["connect", "192.168.1.50:5555"])

    def test_auto_discover_wireless_device(self) -> None:
        client = AdbClient(
            wired_serial="",
            wireless_host="",
            wireless_port=5555,
            prefer_wired=False,
        )

        def fake_run(args, **kwargs):
            if args[:1] == ["devices"]:
                return MagicMock(
                    returncode=0,
                    stdout=(
                        "List of devices attached\n"
                        "192.168.1.77:5555 device product:tv\n"
                    ),
                    stderr="",
                )
            if args[:3] == ["-s", "192.168.1.77:5555", "get-state"]:
                return MagicMock(returncode=0, stdout="device\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(client, "_run_adb", side_effect=fake_run), patch.object(
            client, "_start_health_poll"
        ), patch.object(client, "_set_connected"):
            self.assertTrue(client.connect())


if __name__ == "__main__":
    unittest.main(verbosity=2)
