"""Tests for capture device auto-discovery."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")
from android_tv_connect.capture_device import (
    build_audio_source_segment,
    discover_audio_device,
    discover_video_device,
    is_capture_audio_source,
    pipewiresrc_available,
    resolve_audio_device,
    resolve_video_device,
)
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

    def test_discover_audio_finds_macrosilicon_on_system(self) -> None:
        source = discover_audio_device("534d:2109")
        if source:
            self.assertTrue(is_capture_audio_source(source))

    def test_resolve_audio_prefers_capture_over_builtin(self) -> None:
        cfg = CaptureConfig(
            audio_device="alsa_input.pci-0000_00_1b.0.analog-stereo",
            usb_vendor_product="534d:2109",
        )
        with patch(
            "android_tv_connect.capture_device._usb_capture_present",
            return_value=True,
        ):
            with patch(
                "android_tv_connect.capture_device.discover_audio_device",
                return_value="alsa_input.usb-MACROSILICON_USB3.0_Capture-02.analog-stereo",
            ):
                resolved = resolve_audio_device(cfg)
        self.assertEqual(
            resolved,
            "alsa_input.usb-MACROSILICON_USB3.0_Capture-02.analog-stereo",
        )

    def test_resolve_audio_auto_uses_discovered(self) -> None:
        cfg = CaptureConfig(audio_device="auto", usb_vendor_product="534d:2109")
        with patch(
            "android_tv_connect.capture_device._usb_capture_present",
            return_value=True,
        ):
            with patch(
                "android_tv_connect.capture_device.discover_audio_device",
                return_value="alsa_input.usb-MACROSILICON_USB3.0_Capture-02.analog-stereo",
            ):
                self.assertEqual(
                    resolve_audio_device(cfg),
                    "alsa_input.usb-MACROSILICON_USB3.0_Capture-02.analog-stereo",
                )

    def test_build_audio_source_prefers_pipewiresrc(self) -> None:
        with patch(
            "android_tv_connect.capture_device.pipewiresrc_available",
            return_value=True,
        ):
            segment = build_audio_source_segment(
                "alsa_input.usb-MACROSILICON_USB3.0_Capture-02.analog-stereo"
            )
        self.assertIn("pipewiresrc", segment)
        self.assertIn("target-object=", segment)
        self.assertIn("provide-clock=false", segment)
        self.assertIn("format=S16LE,rate=48000,channels=2", segment)
        self.assertIn("leaky=downstream", segment)

    def test_build_audio_source_falls_back_to_pulsesrc(self) -> None:
        with patch(
            "android_tv_connect.capture_device.pipewiresrc_available",
            return_value=False,
        ):
            segment = build_audio_source_segment("custom.source")
        self.assertIn("pulsesrc", segment)
        self.assertIn("device=custom.source", segment)
        self.assertIn("format=S16LE,rate=48000,channels=2", segment)


if __name__ == "__main__":
    unittest.main(verbosity=2)
