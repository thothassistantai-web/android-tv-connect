"""Tests for MPRIS media controls."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, ".")
from android_tv_connect.mpris_controller import (
    DEFAULT_STREAM_ARTIST,
    DEFAULT_STREAM_TITLE,
    MPRIS_BUS_NAME,
    MPRIS_PLAYER_IFACE,
    MPRIS_ROOT_IFACE,
    PLAYBACK_PAUSED,
    PLAYBACK_PLAYING,
    PLAYBACK_STOPPED,
    CaptureMprisController,
    MprisHandlers,
    build_metadata,
    capture_state_to_playback_status,
)


class CaptureStateMappingTests(unittest.TestCase):
    def test_playing_maps_to_playing(self) -> None:
        self.assertEqual(capture_state_to_playback_status("playing"), PLAYBACK_PLAYING)

    def test_user_paused_maps_to_paused(self) -> None:
        self.assertEqual(
            capture_state_to_playback_status("reconnecting", user_paused=True),
            PLAYBACK_PAUSED,
        )

    def test_stopped_states_map_to_stopped(self) -> None:
        for state in ("stopped", "disconnected", "waiting", "reconnecting", "starting"):
            self.assertEqual(capture_state_to_playback_status(state), PLAYBACK_STOPPED)


class MetadataTests(unittest.TestCase):
    def test_build_metadata_includes_title_and_artist(self) -> None:
        metadata = build_metadata("HDMI Capture", "Onn Stick")
        self.assertEqual(metadata["xesam:title"].get_string(), "HDMI Capture")
        self.assertEqual(metadata["xesam:artist"].get_strv(), ["Onn Stick"])

    def test_build_metadata_omits_artist_when_none(self) -> None:
        metadata = build_metadata("Android TV Connect", None)
        self.assertNotIn("xesam:artist", metadata)


class CaptureMprisControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = CaptureMprisController(identity="Android TV Connect")

    def test_default_properties(self) -> None:
        identity = self.controller.get_property(MPRIS_ROOT_IFACE, "Identity")
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity.get_string(), "Android TV Connect")

        status = self.controller.get_property(MPRIS_PLAYER_IFACE, "PlaybackStatus")
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.get_string(), PLAYBACK_STOPPED)

    def test_playback_status_update(self) -> None:
        self.controller.set_playback_status(PLAYBACK_PLAYING)
        self.assertEqual(self.controller.playback_status, PLAYBACK_PLAYING)
        can_pause = self.controller.get_property(MPRIS_PLAYER_IFACE, "CanPause")
        assert can_pause is not None
        self.assertTrue(can_pause.get_boolean())

    def test_metadata_defaults(self) -> None:
        self.controller.set_metadata()
        metadata = self.controller.get_property(MPRIS_PLAYER_IFACE, "Metadata")
        assert metadata is not None
        unpacked = metadata.unpack()
        self.assertEqual(unpacked["xesam:title"], DEFAULT_STREAM_TITLE)
        self.assertEqual(unpacked["xesam:artist"], [DEFAULT_STREAM_ARTIST])

    def test_play_pause_callbacks_via_idle(self) -> None:
        play_cb = MagicMock()
        pause_cb = MagicMock()
        self.controller.set_handlers(
            MprisHandlers(on_play=play_cb, on_pause=pause_cb),
        )
        invocation = MagicMock()
        self.controller.set_playback_status(PLAYBACK_PAUSED)
        self.controller._handle_method_call(
            None,
            ":1.1",
            "/org/mpris/MediaPlayer2",
            MPRIS_PLAYER_IFACE,
            "Play",
            None,
            invocation,
        )
        self.controller._handle_method_call(
            None,
            ":1.1",
            "/org/mpris/MediaPlayer2",
            MPRIS_PLAYER_IFACE,
            "Pause",
            None,
            invocation,
        )
        invocation.return_value.assert_called()

    def test_volume_setter_invoked(self) -> None:
        setter = MagicMock(return_value=True)
        self.controller.set_handlers(MprisHandlers(set_volume=setter))
        ok = self.controller._handle_set_property(
            None,
            ":1.1",
            "/org/mpris/MediaPlayer2",
            MPRIS_PLAYER_IFACE,
            "Volume",
            __import__("gi").repository.GLib.Variant("d", 0.5),
        )
        self.assertTrue(ok)
        volume = self.controller.get_property(MPRIS_PLAYER_IFACE, "Volume")
        assert volume is not None
        self.assertAlmostEqual(volume.get_double(), 0.5)

    def test_bus_name_constant(self) -> None:
        self.assertEqual(MPRIS_BUS_NAME, "org.mpris.MediaPlayer2.androidtvconnect")


if __name__ == "__main__":
    unittest.main()
