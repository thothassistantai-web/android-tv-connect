"""Tests for capture device auto-discovery."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")
from android_tv_connect.capture_device import discover_video_device, resolve_video_device
from android_tv_connect.config import CaptureConfig


class CaptureDeviceTests(unittest.TestCase):
    def test_resolve_prefers_discovered_node_when_configured_missing(self) -> None:
        cfg = CaptureConfig(video_device="/dev/video0", usb_vendor_product="534d:2109")
        with (
            patch("android_tv_connect.capture_device._supports_mjpeg", return_value=False),
            patch(
                "android_tv_connect.capture_device.discover_video_device",
                return_value="/dev/video1",
            ),
        ):
            self.assertEqual(resolve_video_device(cfg), "/dev/video1")

    def test_discover_finds_ms2109_on_system(self) -> None:
        node = discover_video_device("534d:2109")
        if node:
            self.assertTrue(node.startswith("/dev/video"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
