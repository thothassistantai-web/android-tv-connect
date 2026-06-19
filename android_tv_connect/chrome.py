"""Compact header and chrome auto-hide."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from .input_controller import InputMode, MODE_LABELS
from .branding import APP_NAME


class CompactHeader:
    """Populate an Adw.HeaderBar with status chips and window actions."""

    def __init__(self, header: Adw.HeaderBar, host) -> None:
        self._host = host

        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        left.set_margin_start(4)
        self._capture_chip = self._chip("Capture", self._on_capture_chip)
        self._adb_chip = self._chip("ADB", self._on_adb_chip)
        self._mirror_chip = self._chip("Mirror", self._on_mirror_chip)
        self._mode_chip = Gtk.Label(label=MODE_LABELS[InputMode.LOCAL])
        self._mode_chip.add_css_class("caption")
        self._mode_chip.add_css_class("mode-chip")
        left.append(self._capture_chip)
        left.append(self._adb_chip)
        left.append(self._mirror_chip)
        left.append(self._mode_chip)
        header.pack_start(left)

        title = Gtk.Label(label=APP_NAME)
        title.add_css_class("title")
        header.set_title_widget(title)

        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._pip_btn = self._icon_btn("view-restore-symbolic", "PiP")
        self._fs_btn = self._icon_btn("fullscreen-symbolic", "Fullscreen")
        self._settings_btn = self._icon_btn("emblem-system-symbolic", "Settings")
        self._about_btn = self._icon_btn("help-about-symbolic", "About")
        self._pip_btn.connect("clicked", self._on_pip)
        self._fs_btn.connect("clicked", self._on_fullscreen)
        self._settings_btn.connect("clicked", self._on_settings)
        self._about_btn.connect("clicked", self._on_about)
        right.append(self._pip_btn)
        right.append(self._fs_btn)
        right.append(self._settings_btn)
        right.append(self._about_btn)
        header.pack_end(right)

    def _on_capture_chip(self, *_args) -> None:
        self._host.bump_chrome()
        self._host.on_capture_chip_clicked()

    def _on_adb_chip(self, *_args) -> None:
        self._host.bump_chrome()
        self._host.on_adb_chip_clicked()

    def _on_mirror_chip(self, *_args) -> None:
        self._host.bump_chrome()
        self._host.on_mirror_chip_clicked()

    def _on_pip(self, *_args) -> None:
        self._host.bump_chrome()
        self._host.toggle_pip()

    def _on_fullscreen(self, *_args) -> None:
        self._host.bump_chrome()
        self._host.toggle_fullscreen()

    def _on_settings(self, *_args) -> None:
        self._host.bump_chrome()
        self._host.open_settings()

    def _on_about(self, *_args) -> None:
        self._host.bump_chrome()
        self._host.open_about()

    def _chip(self, name: str, on_click) -> Gtk.Button:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        dot = Gtk.Label(label="●")
        dot.add_css_class("status-bad")
        text = Gtk.Label(label=name)
        text.add_css_class("caption")
        box.append(dot)
        box.append(text)

        btn = Gtk.Button()
        btn.set_has_frame(False)
        btn.add_css_class("flat")
        btn.add_css_class("status-chip-btn")
        btn.set_child(box)
        btn.set_tooltip_text(name)
        btn.connect("clicked", on_click)
        btn._dot = dot  # type: ignore[attr-defined]
        btn._label = text  # type: ignore[attr-defined]
        return btn

    def _icon_btn(self, icon: str, tip: str) -> Gtk.Button:
        btn = Gtk.Button()
        btn.set_has_frame(False)
        btn.set_tooltip_text(tip)
        btn.set_child(Gtk.Image.new_from_icon_name(icon))
        btn.add_css_class("flat")
        btn.set_size_request(34, 34)
        return btn

    @property
    def capture_chip(self) -> Gtk.Button:
        return self._capture_chip

    @property
    def adb_chip(self) -> Gtk.Button:
        return self._adb_chip

    @property
    def mirror_chip(self) -> Gtk.Button:
        return self._mirror_chip

    @property
    def pip_button(self) -> Gtk.Button:
        return self._pip_btn

    @property
    def fullscreen_button(self) -> Gtk.Button:
        return self._fs_btn

    def set_mode_label(self, text: str) -> None:
        self._mode_chip.set_text(text)

    def set_dot(self, chip: Gtk.Button, ok: bool) -> None:
        dot = chip._dot  # type: ignore[attr-defined]
        dot.remove_css_class("status-ok")
        dot.remove_css_class("status-bad")
        dot.add_css_class("status-ok" if ok else "status-bad")

    def set_chip_tooltip(self, chip: Gtk.Button, text: str) -> None:
        chip.set_tooltip_text(text)

    def update_tooltips(self, pip: str, fullscreen: str) -> None:
        self._pip_btn.set_tooltip_text(f"Picture-in-Picture ({pip})")
        self._fs_btn.set_tooltip_text(f"Fullscreen ({fullscreen})")


class ChromeAutoHide:
    """Auto-hide the bottom control overlay when idle."""

    def __init__(
        self,
        controls_shell,
        *,
        delay_ms: int = 2500,
        enabled: bool = True,
        move_threshold: float = 3.0,
    ) -> None:
        self._controls = controls_shell
        self._delay_ms = delay_ms
        self._enabled = enabled
        self._move_threshold = move_threshold
        self._timer: int | None = None
        self._pinned = False
        self._last_x: float | None = None
        self._last_y: float | None = None

    def set_delay_ms(self, delay_ms: int) -> None:
        self._delay_ms = max(500, delay_ms)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled:
            self.show_all()
            self._arm_hide_timer()
        else:
            self.cancel()
            self._controls.show_auto()
            self._controls.force_show()

    def set_pinned(self, pinned: bool) -> None:
        self._pinned = pinned
        if pinned:
            self.show_all()
            self.cancel()

    def show_all(self) -> None:
        self._controls.show_auto()
        if not self._controls.user_collapsed:
            self._controls.force_show()

    def record_pointer(self, x: float, y: float) -> None:
        if not self._pointer_moved(x, y):
            return
        self._last_x = x
        self._last_y = y
        self.record_activity()

    def record_activity(self) -> None:
        self.show_all()
        if not self._enabled or self._pinned:
            return
        self._arm_hide_timer()

    def bump(self) -> None:
        self.record_activity()

    def schedule_hide(self) -> None:
        if not self._enabled or self._pinned:
            return
        self.show_all()
        self._arm_hide_timer()

    def cancel(self) -> None:
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    def _pointer_moved(self, x: float, y: float) -> bool:
        if self._last_x is None or self._last_y is None:
            return True
        return (
            abs(x - self._last_x) >= self._move_threshold
            or abs(y - self._last_y) >= self._move_threshold
        )

    def _arm_hide_timer(self) -> None:
        self.cancel()
        self._timer = GLib.timeout_add(self._delay_ms, self._hide)

    def _hide(self) -> bool:
        self._timer = None
        if not self._enabled or self._pinned:
            return False
        self._controls.hide_auto()
        return False
