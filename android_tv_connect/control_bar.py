"""On-screen remote — collapsible overlay bar."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from . import adb_client
from .adb_client import AdbClient
from .input_controller import InputMode

_BTN = 32
_DPAD = 28


class ControlBar(Gtk.Box):
    """Compact remote: D-pad + transport in one row (no chrome wrapper)."""

    def __init__(self, adb: AdbClient, host) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._adb = adb
        self._host = host
        self._repeat_source: int | None = None
        self._repeat_code: int | None = None

        self.append(self._dpad_cluster())

        self.append(self._vsep())

        for w in (
            self._act("system-shutdown-symbolic", "Power", power=True),
            self._act("go-home-symbolic", "Home", code=adb_client.KEYCODE_HOME),
            self._act("go-previous-symbolic", "Back", code=adb_client.KEYCODE_BACK),
        ):
            self.append(w)

        self.append(self._vsep())

        for w in (
            self._act("audio-volume-low-symbolic", "Vol −", code=adb_client.KEYCODE_VOLUME_DOWN, repeat=True),
            self._act("audio-volume-muted-symbolic", "Mute", code=adb_client.KEYCODE_VOLUME_MUTE),
            self._act("audio-volume-high-symbolic", "Vol +", code=adb_client.KEYCODE_VOLUME_UP, repeat=True),
            self._act("media-playback-start-symbolic", "Play", code=adb_client.KEYCODE_MEDIA_PLAY_PAUSE),
            self._act("preferences-desktop-remote-desktop-symbolic", "TV Settings", code=adb_client.KEYCODE_SETTINGS),
        ):
            self.append(w)

        self.append(self._vsep())

        self._mouse_toggle = self._toggle("input-mouse-symbolic", "Mouse mode")
        self._keyboard_toggle = self._toggle("input-keyboard-symbolic", "Keyboard capture")
        self._mouse_toggle.connect("toggled", self._on_mouse_toggled)
        self._keyboard_toggle.connect("toggled", self._on_keyboard_toggled)
        self.append(self._mouse_toggle)
        self.append(self._keyboard_toggle)

    def _vsep(self) -> Gtk.Separator:
        s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        s.set_margin_start(2)
        s.set_margin_end(2)
        return s

    def _toggle(self, icon: str, tip: str) -> Gtk.ToggleButton:
        b = Gtk.ToggleButton()
        b.set_has_frame(False)
        b.set_tooltip_text(tip)
        b.set_child(Gtk.Image.new_from_icon_name(icon))
        b.set_size_request(_BTN, _BTN)
        return b

    def _dpad_cluster(self) -> Gtk.Grid:
        grid = Gtk.Grid()
        grid.set_row_spacing(1)
        grid.set_column_spacing(1)
        specs = [
            (0, 1, "go-up-symbolic", "Up", adb_client.KEYCODE_DPAD_UP),
            (1, 0, "go-previous-symbolic", "Left", adb_client.KEYCODE_DPAD_LEFT),
            (1, 1, "media-playback-start-symbolic", "OK", adb_client.KEYCODE_DPAD_CENTER),
            (1, 2, "go-next-symbolic", "Right", adb_client.KEYCODE_DPAD_RIGHT),
            (2, 1, "go-down-symbolic", "Down", adb_client.KEYCODE_DPAD_DOWN),
        ]
        for row, col, icon, tip, code in specs:
            btn = Gtk.Button()
            btn.set_has_frame(False)
            btn.set_tooltip_text(tip)
            btn.set_child(Gtk.Image.new_from_icon_name(icon))
            btn.set_size_request(_DPAD, _DPAD)
            btn.connect("clicked", lambda _b, c=code: self._send_key(c))
            grid.attach(btn, col, row, 1, 1)
        return grid

    def _act(
        self,
        icon: str,
        tip: str,
        *,
        code: int | None = None,
        repeat: bool = False,
        power: bool = False,
    ) -> Gtk.Button:
        btn = Gtk.Button()
        btn.set_has_frame(False)
        btn.set_tooltip_text(tip)
        btn.set_child(Gtk.Image.new_from_icon_name(icon))
        btn.set_size_request(_BTN, _BTN)
        if power:
            btn.connect("clicked", self._on_power)
        elif repeat and code is not None:
            self._bind_repeat(btn, code)
        elif code is not None:
            btn.connect("clicked", lambda _b, c=code: self._send_key(c))
        return btn

    def set_mouse_mode(self, active: bool) -> None:
        if self._mouse_toggle.get_active() != active:
            self._mouse_toggle.set_active(active)

    def set_input_mode(self, mode: InputMode) -> None:
        active = mode == InputMode.KEYBOARD
        if self._keyboard_toggle.get_active() != active:
            self._keyboard_toggle.set_active(active)

    def set_keyboard_capture(self, active: bool) -> None:
        self.set_input_mode(InputMode.KEYBOARD if active else InputMode.LOCAL)

    def _bump(self) -> None:
        bump = getattr(self._host, "bump_chrome", None)
        if callable(bump):
            bump()

    def _send_key(self, code: int) -> bool:
        self._bump()
        if self._adb.is_connected():
            self._adb.keyevent(code)
            return True
        return False

    def _on_mouse_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._host.set_mouse_mode(btn.get_active())

    def _on_keyboard_toggled(self, btn: Gtk.ToggleButton) -> None:
        if btn.get_active():
            self._host.set_input_mode(InputMode.KEYBOARD, source="toggle")
        elif self._host.input_mode == InputMode.KEYBOARD:
            self._host.set_input_mode(InputMode.REMOTE, source="toggle")

    def _on_power(self, btn: Gtk.Button) -> None:
        d = Adw.MessageDialog(
            transient_for=btn.get_root(),
            heading="Power",
            body="Send power key to the Android TV stick?",
        )
        d.add_response("cancel", "Cancel")
        d.add_response("power", "Power")
        d.set_response_appearance("power", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")
        d.connect("response", lambda _d, r: self._send_key(adb_client.KEYCODE_POWER) if r == "power" else None)
        d.present()

    def _bind_repeat(self, btn: Gtk.Button, code: int) -> None:
        ctrl = Gtk.GestureClick()
        ctrl.set_button(0)
        ctrl.connect("pressed", lambda *_: (self._send_key(code), self._start_repeat(code)))
        ctrl.connect("released", lambda *_: self._stop_repeat())
        btn.add_controller(ctrl)

    def _repeat_tick(self) -> bool:
        if self._repeat_code is None:
            return False
        self._send_key(self._repeat_code)
        return True

    def _start_repeat(self, code: int) -> None:
        from gi.repository import GLib

        self._stop_repeat()
        self._repeat_code = code
        self._repeat_source = GLib.timeout_add(150, self._repeat_tick)

    def _stop_repeat(self) -> None:
        from gi.repository import GLib

        self._repeat_code = None
        if self._repeat_source is not None:
            GLib.source_remove(self._repeat_source)
            self._repeat_source = None


class ControlBarShell(Gtk.Box):
    """Collapsible wrapper: remote bar, hide button, and expand pill."""

    def __init__(self, adb: AdbClient, host) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_halign(Gtk.Align.CENTER)
        self.set_margin_bottom(6)
        self.set_margin_start(8)
        self.set_margin_end(8)
        self._host = host
        self._user_collapsed = host.config.window.control_bar_collapsed
        self._auto_hidden = False

        self._bar_revealer = Gtk.Revealer()
        self._bar_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self._bar_revealer.set_transition_duration(180)
        self._bar_revealer.set_reveal_child(not self._user_collapsed)

        bar_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar_row.add_css_class("control-overlay")
        self._bar = ControlBar(adb, host)
        bar_row.append(self._bar)

        hide_tip = "Hide remote bar"
        shortcut = getattr(host.config.shortcuts, "control_bar_toggle", "<Shift>F7")
        from .shortcuts import humanize_shortcut

        hide_tip = f"Hide remote bar ({humanize_shortcut(shortcut)})"
        self._hide_btn = Gtk.Button()
        self._hide_btn.set_has_frame(False)
        self._hide_btn.set_tooltip_text(hide_tip)
        self._hide_btn.set_child(Gtk.Image.new_from_icon_name("pan-down-symbolic"))
        self._hide_btn.set_size_request(28, 28)
        self._hide_btn.connect("clicked", self._on_hide_clicked)
        bar_row.append(self._hide_btn)
        self._bar_revealer.set_child(bar_row)

        self._expand_revealer = Gtk.Revealer()
        self._expand_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self._expand_revealer.set_transition_duration(180)
        self._expand_revealer.set_reveal_child(self._user_collapsed)

        expand_btn = Gtk.Button()
        expand_btn.add_css_class("control-expand-pill")
        expand_btn.set_tooltip_text(f"Show remote bar ({humanize_shortcut(shortcut)})")
        expand_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        expand_box.append(Gtk.Image.new_from_icon_name("pan-up-symbolic"))
        expand_box.append(Gtk.Label(label="Remote"))
        expand_btn.set_child(expand_box)
        expand_btn.connect("clicked", self._on_expand_clicked)
        self._expand_revealer.set_child(expand_btn)

        self.append(self._bar_revealer)
        self.append(self._expand_revealer)

    @property
    def bar(self) -> ControlBar:
        return self._bar

    @property
    def user_collapsed(self) -> bool:
        return self._user_collapsed

    @property
    def bar_visible(self) -> bool:
        return self._bar_revealer.get_reveal_child()

    def set_mouse_mode(self, active: bool) -> None:
        self._bar.set_mouse_mode(active)

    def set_input_mode(self, mode: InputMode) -> None:
        self._bar.set_input_mode(mode)

    def collapse_user(self) -> None:
        self._user_collapsed = True
        self._auto_hidden = False
        self._sync()
        self._host.persist_control_bar_collapsed(True)

    def expand_user(self) -> None:
        self._user_collapsed = False
        self._auto_hidden = False
        self._sync()
        self._host.persist_control_bar_collapsed(False)
        self._host.bump_chrome()

    def toggle_user(self) -> None:
        if self._user_collapsed or not self.bar_visible:
            self.expand_user()
        else:
            self.collapse_user()

    def hide_auto(self) -> None:
        self._auto_hidden = True
        self._sync()

    def show_auto(self) -> None:
        self._auto_hidden = False
        self._sync()

    def force_show(self) -> None:
        """Reveal bar for PiP/focus paths that need controls visible."""
        self._auto_hidden = False
        if not self._user_collapsed:
            self._bar_revealer.set_reveal_child(True)
            self._expand_revealer.set_reveal_child(False)

    def _sync(self) -> None:
        show_bar = not self._auto_hidden and not self._user_collapsed
        self._bar_revealer.set_reveal_child(show_bar)
        self._expand_revealer.set_reveal_child(not show_bar)

    def _on_hide_clicked(self, *_args) -> None:
        self.collapse_user()

    def _on_expand_clicked(self, *_args) -> None:
        self.expand_user()
