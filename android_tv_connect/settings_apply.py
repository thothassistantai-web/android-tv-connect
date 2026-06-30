"""Non-blocking settings preview, revert, and commit for the main window."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from .config import AppConfig
from .settings_draft import config_snapshot
from .settings_store import save_config

if TYPE_CHECKING:
    from .window import MainWindow

LOG = logging.getLogger(__name__)

LIVE_PREVIEW_DEBOUNCE_MS = 300


class SettingsApplyController:
    """Debounce live preview and run save/apply off the GTK main loop."""

    def __init__(self, window: MainWindow) -> None:
        self._window = window
        self._preview_timer: int | None = None
        self._preview_pending: AppConfig | None = None
        self._generation = 0

    def cancel_pending(self) -> None:
        """Drop debounced preview work (e.g. when settings closes)."""
        if self._preview_timer is not None:
            GLib.source_remove(self._preview_timer)
            self._preview_timer = None
        self._preview_pending = None
        self._generation += 1
        self._window.cancel_live_capture_apply()

    def schedule_live_preview(self, config: AppConfig) -> None:
        self._preview_pending = config
        if self._preview_timer is not None:
            return
        self._preview_timer = GLib.timeout_add(
            LIVE_PREVIEW_DEBOUNCE_MS,
            self._flush_live_preview,
        )

    def schedule_revert(self, saved: AppConfig) -> None:
        self.cancel_pending()
        generation = self._generation
        saved_copy = config_snapshot(saved)
        GLib.idle_add(self._deliver_revert, generation, saved_copy)

    def commit_saved(
        self,
        config: AppConfig,
        on_complete: Callable[[AppConfig], None] | None = None,
    ) -> None:
        self.cancel_pending()
        generation = self._generation
        config_copy = config_snapshot(config)

        def work() -> None:
            try:
                save_config(config_copy)
            except Exception:
                LOG.exception("Failed to save settings")
            GLib.idle_add(self._deliver_commit, generation, config_copy, on_complete)

        threading.Thread(target=work, daemon=True, name="settings-save").start()

    def _flush_live_preview(self) -> bool:
        self._preview_timer = None
        config = self._preview_pending
        self._preview_pending = None
        if config is None:
            return False
        generation = self._generation
        GLib.idle_add(self._deliver_preview, generation, config)
        return False

    def _deliver_preview(self, generation: int, config: AppConfig) -> bool:
        if generation != self._generation:
            return False
        self._window.apply_settings_core(config)
        return False

    def _deliver_revert(self, generation: int, saved: AppConfig) -> bool:
        if generation != self._generation:
            return False
        self._window.apply_settings_core(saved)
        return False

    def _deliver_commit(
        self,
        generation: int,
        config: AppConfig,
        on_complete: Callable[[AppConfig], None] | None,
    ) -> bool:
        if generation != self._generation:
            return False
        self._window.apply_settings_core(config)
        if on_complete is not None:
            on_complete(config)
        return False
