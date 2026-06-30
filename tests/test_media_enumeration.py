"""Tests for PipeWire/Pulse audio source enumeration."""

from __future__ import annotations

import sys
import unittest

sys.path.insert(0, ".")
from android_tv_connect.media_enumeration import (
    AudioSourceOption,
    _parse_pactl_sources_short,
    _parse_pactl_sources_verbose,
    _parse_pw_cli_nodes,
)


class MediaEnumerationTests(unittest.TestCase):
    def test_parse_pactl_verbose_skips_monitors(self) -> None:
        stdout = """
Source #1
	Name: alsa_output.pci.monitor
	Description: Monitor of Built-in Audio
Source #2
	Name: alsa_input.pci.analog-stereo
	Description: Built-in Audio Analog Stereo
"""
        parsed = _parse_pactl_sources_verbose(stdout)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].name, "alsa_input.pci.analog-stereo")
        self.assertEqual(parsed[0].description, "Built-in Audio Analog Stereo")

    def test_parse_pactl_short_uses_name_not_state(self) -> None:
        stdout = (
            "59\talsa_input.pci.analog-stereo\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED\n"
            "975\talsa_input.usb-MACROSILICON.analog-stereo\tPipeWire\ts16le 2ch 48000Hz\tRUNNING\n"
        )
        parsed = _parse_pactl_sources_short(stdout)
        self.assertEqual(
            [source.description for source in parsed],
            [
                "alsa_input.pci.analog-stereo",
                "alsa_input.usb-MACROSILICON.analog-stereo",
            ],
        )

    def test_parse_pw_cli_nodes_finds_audio_sources(self) -> None:
        stdout = """
 		node.description = "USB3.0 Capture Analog Stereo"
 		node.name = "alsa_input.usb-MACROSILICON.analog-stereo"
 		media.class = "Audio/Source"
 		node.description = "Built-in Audio Analog Stereo"
 		node.name = "alsa_input.pci.analog-stereo"
 		media.class = "Audio/Source"
"""
        parsed = _parse_pw_cli_nodes(stdout)
        self.assertEqual(
            parsed,
            [
                AudioSourceOption(
                    name="alsa_input.usb-MACROSILICON.analog-stereo",
                    description="USB3.0 Capture Analog Stereo",
                ),
                AudioSourceOption(
                    name="alsa_input.pci.analog-stereo",
                    description="Built-in Audio Analog Stereo",
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
