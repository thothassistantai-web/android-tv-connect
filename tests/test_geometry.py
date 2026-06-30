#!/usr/bin/env python3
"""Window geometry helpers used for WM tile/snap behavior."""

from __future__ import annotations

import sys
import unittest

sys.path.insert(0, ".")
from android_tv_connect.geometry import (
    MIN_NORMAL_WIDTH,
    MIN_VIDEO_HEIGHT,
    TILED_TOPLEVEL_STATE_MASK,
    _TOPLEVEL_STATE_LEFT_TILED,
    _TOPLEVEL_STATE_RIGHT_TILED,
    is_tiled_toplevel_state,
    min_normal_window_size,
    video_area_height,
    window_height_for_width,
)


class GeometryHelpersTests(unittest.TestCase):
    def test_min_normal_width_allows_half_screen_on_small_displays(self) -> None:
        """Half of an 800px-wide display is 400px; min width must stay below that."""
        width, height = min_normal_window_size()
        self.assertEqual(width, MIN_NORMAL_WIDTH)
        self.assertLess(width, 400)
        self.assertEqual(height, window_height_for_width(MIN_NORMAL_WIDTH))

    def test_video_area_height_respects_minimum(self) -> None:
        self.assertGreaterEqual(video_area_height(100), MIN_VIDEO_HEIGHT)

    def test_is_tiled_toplevel_state_detects_edge_tiles(self) -> None:
        self.assertFalse(is_tiled_toplevel_state(0))
        self.assertTrue(is_tiled_toplevel_state(_TOPLEVEL_STATE_LEFT_TILED))
        self.assertTrue(is_tiled_toplevel_state(_TOPLEVEL_STATE_RIGHT_TILED))
        self.assertTrue(is_tiled_toplevel_state(TILED_TOPLEVEL_STATE_MASK))

    def test_aspect_height_for_snap_width(self) -> None:
        """Document expected 16:9 body height when snapped to ~960px width."""
        half_panel_width = 960
        expected = window_height_for_width(half_panel_width)
        self.assertEqual(expected, video_area_height(half_panel_width) + 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
