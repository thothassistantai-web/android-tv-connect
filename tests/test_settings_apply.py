#!/usr/bin/env python3
"""Tests for non-blocking settings apply coordinator."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from android_tv_connect.config import default_config
from android_tv_connect.settings_apply import SettingsApplyController, LIVE_PREVIEW_DEBOUNCE_MS


class SettingsApplyControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = MagicMock()
        self.window._config = default_config()
        self.controller = SettingsApplyController(self.window)

    def test_cancel_pending_increments_generation(self) -> None:
        before = self.controller._generation
        self.controller.cancel_pending()
        self.assertEqual(self.controller._generation, before + 1)
        self.window.cancel_live_capture_apply.assert_called_once()

    def test_stale_preview_delivery_is_ignored(self) -> None:
        config = default_config()
        generation = self.controller._generation
        self.controller._deliver_preview(generation - 1, config)
        self.window.apply_settings_core.assert_not_called()

    def test_commit_applies_after_save_on_idle(self) -> None:
        config = replace(default_config(), watch_poll_interval_s=7.5)
        saved = []

        def on_complete(cfg):
            saved.append(cfg)

        with patch("android_tv_connect.settings_apply.save_config") as mock_save:
            with patch("android_tv_connect.settings_apply.GLib") as mock_glib:
                mock_glib.idle_add.side_effect = lambda fn, *args: fn(*args) or False

                def run_thread(*, target, **kwargs):
                    target()
                    thread = MagicMock()
                    thread.start = MagicMock()
                    return thread

                with patch("threading.Thread", side_effect=run_thread):
                    self.controller.commit_saved(config, on_complete)

        mock_save.assert_called_once()
        self.window.apply_settings_core.assert_called_once()
        self.assertEqual(len(saved), 1)

    def test_live_preview_debounce_constant(self) -> None:
        self.assertEqual(LIVE_PREVIEW_DEBOUNCE_MS, 300)


if __name__ == "__main__":
    unittest.main(verbosity=2)
