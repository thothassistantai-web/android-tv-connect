#!/usr/bin/env python3
"""Tests for PiP geometry persistence and layering helpers."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from android_tv_connect.config import PipWindowConfig, default_config
from android_tv_connect.geometry import (
    PIP_OPACITY_MAX,
    PIP_OPACITY_MIN,
    SavedGeometry,
    clamp_pip_opacity,
    load_window_state,
    pip_corner_position,
    save_window_state,
    window_move,
    window_xy,
)
from android_tv_connect.pip_window import (
    apply_borderless,
    click_after_drag,
    set_window_layering,
)


class PipGeometryTests(unittest.TestCase):
    def test_pip_corner_bottom_right(self) -> None:
        monitor = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        x, y = pip_corner_position("bottom-right", 480, 270, monitor, margin=16)
        self.assertEqual(x, 1920 - 480 - 16)
        self.assertEqual(y, 1080 - 270 - 16)

    def test_window_xy_without_native_move(self) -> None:
        window = SimpleNamespace()
        self.assertEqual(window_xy(window), (0, 0))

    def test_window_move_noop_when_unsupported(self) -> None:
        window = SimpleNamespace()
        window_move(window, 10, 20)

    def test_pip_config_min_size_defaults(self) -> None:
        pip = default_config().window.pip
        self.assertGreaterEqual(pip.min_width, 320)
        self.assertGreaterEqual(pip.min_height, 180)
        self.assertTrue(pip.keep_above_default)

    def test_window_state_persists_pip_keep_above(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp)
            state_path = cfg_dir / "window.json"
            import android_tv_connect.geometry as geometry

            original = geometry.WINDOW_STATE_PATH
            geometry.WINDOW_STATE_PATH = state_path
            try:
                state = load_window_state()
                state["pip_keep_above"] = False
                state["pip"] = SavedGeometry(400, 250, 100, 200)
                save_window_state(state)
                loaded = json.loads(state_path.read_text())
                self.assertFalse(loaded["pip_keep_above"])
                self.assertEqual(loaded["pip"]["width"], 400)
            finally:
                geometry.WINDOW_STATE_PATH = original


class PipLayeringTests(unittest.TestCase):
    def test_set_window_layering_calls_surface(self) -> None:
        surface = MagicMock()
        window = MagicMock()
        window.get_surface.return_value = surface
        set_window_layering(window, above=True, below=False)
        surface.set_keep_above.assert_called_once_with(True)
        surface.set_keep_below.assert_called_once_with(False)


class PipOpacityTests(unittest.TestCase):
    def test_opacity_bounds(self) -> None:
        self.assertEqual(PIP_OPACITY_MIN, 0.25)
        self.assertEqual(PIP_OPACITY_MAX, 1.0)

    def test_clamp_pip_opacity(self) -> None:
        self.assertEqual(clamp_pip_opacity(0.0), PIP_OPACITY_MIN)
        self.assertEqual(clamp_pip_opacity(0.1), PIP_OPACITY_MIN)
        self.assertEqual(clamp_pip_opacity(0.25), 0.25)
        self.assertEqual(clamp_pip_opacity(0.75), 0.75)
        self.assertEqual(clamp_pip_opacity(1.0), 1.0)
        self.assertEqual(clamp_pip_opacity(1.5), PIP_OPACITY_MAX)

    def test_window_state_clamps_legacy_low_opacity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp)
            state_path = cfg_dir / "window.json"
            import android_tv_connect.geometry as geometry

            original = geometry.WINDOW_STATE_PATH
            geometry.WINDOW_STATE_PATH = state_path
            try:
                state_path.write_text('{"pip_opacity": 0.05}\n')
                state = load_window_state()
                self.assertEqual(state["pip_opacity"], PIP_OPACITY_MIN)
            finally:
                geometry.WINDOW_STATE_PATH = original


class PipChromeTests(unittest.TestCase):
    def test_click_after_drag_ignores_large_movement(self) -> None:
        self.assertFalse(click_after_drag(12.0, 0.0))
        self.assertFalse(click_after_drag(0.0, -10.0))

    def test_click_after_drag_accepts_small_jitter(self) -> None:
        self.assertTrue(click_after_drag(2.0, 3.0))
        self.assertTrue(click_after_drag(0.0, 0.0))

    @patch("android_tv_connect.pip_window._window_toplevel")
    def test_apply_borderless_strips_decorations(self, mock_toplevel) -> None:
        toplevel = MagicMock()
        mock_toplevel.return_value = toplevel
        window = MagicMock()
        apply_borderless(window)
        window.set_decorated.assert_called_once_with(False)
        toplevel.set_decorated.assert_called_once_with(False)

    def test_apply_borderless_without_toplevel(self) -> None:
        window = MagicMock()
        window.get_surface.return_value = None
        apply_borderless(window)
        window.set_decorated.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
