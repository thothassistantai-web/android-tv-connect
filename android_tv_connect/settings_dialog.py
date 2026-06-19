"""Application settings dialog."""

from __future__ import annotations

from dataclasses import replace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from .adb_settings import (
    normalize_adb_config,
    normalize_wired_serial,
    normalize_wireless_host,
    parse_wireless_port,
)
from .branding import APP_NAME, VERSION
from .config import AdbConfig, AppConfig
from .settings_store import save_config
from .shortcuts import SHORTCUT_DEFINITIONS, SHORTCUT_HELP, validate_shortcut


class SettingsDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window, config: AppConfig, on_saved) -> None:
        super().__init__(transient_for=parent, modal=True)
        self._config = config
        self._on_saved = on_saved
        self.set_title("Android TV Connect Settings")
        self.set_default_size(520, 640)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(title="Settings", subtitle="Android TV Connect")
        )
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", self._on_cancel)
        header.pack_start(cancel)
        done = Gtk.Button(label="Save")
        done.add_css_class("suggested-action")
        done.connect("clicked", self._on_save)
        header.pack_end(done)
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()
        page.add(self._adb_group())
        page.add(self._capture_group())
        page.add(self._input_group())
        page.add(self._shortcuts_group())
        page.add(self._window_group())
        page.add(self._about_group())
        page.add(self._watch_group())

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(page)
        toolbar.set_content(scrolled)
        self.set_content(toolbar)
        self.connect("close-request", self._on_close_request)

    def _on_close_request(self, *_args) -> bool:
        return False

    def _on_cancel(self, *_args) -> None:
        self.close()

    def _show_error(self, heading: str, body: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=heading,
            body=body,
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def _entry_row(self, title: str, subtitle: str, value: str) -> Adw.EntryRow:
        row = Adw.EntryRow(title=title)
        row.set_text(value)
        row.set_tooltip_text(subtitle or None)
        return row

    def _spin_row(
        self, title: str, value: float, step: float, lower: float, upper: float
    ) -> Adw.SpinRow:
        adj = Gtk.Adjustment.new(value, lower, upper, step, step * 5, 0)
        return Adw.SpinRow(title=title, adjustment=adj, digits=1 if step < 1 else 0)

    def _switch_row(self, title: str, subtitle: str, active: bool) -> Adw.SwitchRow:
        return Adw.SwitchRow(title=title, subtitle=subtitle, active=active)

    def _adb_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="ADB Connection")
        self._wired_serial = self._entry_row(
            "Wired serial",
            "USB serial, or auto to use the first connected device",
            self._config.adb.wired_serial or "auto",
        )
        self._wireless_host = self._entry_row(
            "Wireless host", "Fallback IP when USB unavailable", self._config.adb.wireless_host
        )
        self._wireless_port = self._entry_row(
            "Wireless port", "TCP port for network ADB", str(self._config.adb.wireless_port)
        )
        self._prefer_wired = self._switch_row(
            "Prefer wired ADB",
            "Use USB debugging before attempting wireless connect",
            self._config.input.prefer_wired_adb,
        )
        group.add(self._wired_serial)
        group.add(self._wireless_host)
        group.add(self._wireless_port)
        group.add(self._prefer_wired)
        return group

    def _capture_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Capture Device")
        self._video_device = self._entry_row(
            "Video device",
            "Use auto or a V4L2 node such as /dev/video1",
            self._config.capture.video_device,
        )
        self._audio_device = self._entry_row(
            "Audio device",
            "PipeWire source name for capture audio",
            self._config.capture.audio_device,
        )
        self._width = self._spin_row("Width", self._config.capture.width, 1, 640, 3840)
        self._height = self._spin_row("Height", self._config.capture.height, 1, 480, 2160)
        self._fps = self._spin_row("Framerate", self._config.capture.framerate, 1, 15, 60)
        group.add(self._video_device)
        group.add(self._audio_device)
        group.add(self._width)
        group.add(self._height)
        group.add(self._fps)
        return group

    def _input_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Input and Control")
        self._auto_kbd = self._switch_row(
            "Click video to control",
            "Single click arms remote mode (arrows, scroll, tap). Double-click for keyboard.",
            self._config.input.click_to_control,
        )
        self._release_unfocus = self._switch_row(
            "Release when unfocused",
            "Return to local mode when another app is focused",
            self._config.input.release_on_unfocus,
        )
        self._release_esc = self._switch_row(
            "Esc releases control",
            "Escape stops remote/keyboard capture without sending Back",
            self._config.input.release_on_escape,
        )
        self._scroll = self._spin_row(
            "Scroll threshold", self._config.input.scroll_threshold, 1, 2, 40
        )
        self._soft_unfocused = self._switch_row(
            "Soft buttons when unfocused",
            "Volume and remote buttons work while using other apps",
            self._config.input.soft_buttons_work_unfocused,
        )
        self._default_mode = Adw.ComboRow(title="Default pointer mode")
        modes = Gtk.StringList.new(["D-pad navigation", "Mouse / touch"])
        self._default_mode.set_model(modes)
        self._default_mode.set_selected(
            0 if self._config.input.default_pointer_mode == "nav" else 1
        )
        group.add(self._auto_kbd)
        group.add(self._release_unfocus)
        group.add(self._release_esc)
        group.add(self._scroll)
        group.add(self._soft_unfocused)
        group.add(self._default_mode)
        return group

    def _shortcuts_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Keyboard Shortcuts")
        group.set_description(SHORTCUT_HELP)
        self._shortcut_rows: dict[str, Adw.EntryRow] = {}
        for action_id, label, default in SHORTCUT_DEFINITIONS:
            row = self._entry_row(label, f"Default: {default}", self._config.shortcuts.get(action_id))
            self._shortcut_rows[action_id] = row
            group.add(row)
        return group

    def _window_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Window")
        self._remember_geo = self._switch_row(
            "Remember geometry",
            "Restore window size and mode on launch",
            self._config.window.remember_geometry,
        )
        self._aspect = self._switch_row(
            "Lock 16:9 aspect",
            "Keep window aspect ratio when resizing",
            self._config.window.aspect_ratio_locked,
        )
        self._pip_hide = self._switch_row(
            "PiP bar auto-hide",
            "Hide soft buttons in PiP when unfocused",
            self._config.window.pip.soft_bar_auto_hide,
        )
        self._pip_opacity = self._spin_row(
            "PiP opacity", self._config.window.pip.opacity, 0.05, 0.3, 1.0
        )
        self._chrome_hide = self._switch_row(
            "Auto-hide chrome",
            "Hide header and remote bar after idle — even if the mouse is still over the window",
            self._config.window.chrome_auto_hide,
        )
        self._chrome_delay = self._spin_row(
            "Chrome hide delay (ms)",
            self._config.window.chrome_hide_delay_ms,
            250,
            1000,
            15000,
        )
        self._banner_hide = self._spin_row(
            "Control banner hide (ms)",
            self._config.window.banner_auto_hide_ms,
            500,
            0,
            30000,
        )
        self._bar_collapsed = self._switch_row(
            "Start with remote hidden",
            "Collapse the bottom remote bar on launch",
            self._config.window.control_bar_collapsed,
        )
        group.add(self._remember_geo)
        group.add(self._aspect)
        group.add(self._chrome_hide)
        group.add(self._chrome_delay)
        group.add(self._banner_hide)
        group.add(self._bar_collapsed)
        group.add(self._pip_hide)
        group.add(self._pip_opacity)
        return group

    def _about_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="About")
        row = Adw.ActionRow(title=APP_NAME, subtitle=f"Version {VERSION}")
        row.set_activatable(False)
        group.add(row)
        return group

    def _watch_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Auto-start")
        self._watch_enable = self._switch_row(
            "Enable device watcher",
            "Auto-launch when capture dongle and ADB device are ready",
            self._config.watch_autostart_enabled,
        )
        self._watch_poll = self._spin_row(
            "Poll interval (s)", self._config.watch_poll_interval_s, 0.5, 1, 30
        )
        group.add(self._watch_enable)
        group.add(self._watch_poll)
        return group

    def _on_save(self, *_args) -> None:
        shortcuts_kwargs = {}
        for action_id, _label, _default in SHORTCUT_DEFINITIONS:
            value = self._shortcut_rows[action_id].get_text().strip()
            ok, err = validate_shortcut(value)
            if not ok:
                self._show_error("Invalid shortcut", f"{_label}: {err}")
                return
            shortcuts_kwargs[action_id] = value

        shortcuts = replace(self._config.shortcuts, **shortcuts_kwargs)

        wireless_host_raw = self._wireless_host.get_text().strip()
        port, port_err = parse_wireless_port(self._wireless_port.get_text())
        if port_err:
            self._show_error("Invalid wireless port", port_err)
            return

        adb = replace(
            self._config.adb,
            wired_serial=normalize_wired_serial(self._wired_serial.get_text()),
            wireless_host=normalize_wireless_host(
                wireless_host_raw, default=AdbConfig.wireless_host
            ),
            wireless_port=port,
        )
        adb = normalize_adb_config(adb)
        capture = replace(
            self._config.capture,
            video_device=self._video_device.get_text().strip(),
            audio_device=self._audio_device.get_text().strip(),
            width=int(self._width.get_value()),
            height=int(self._height.get_value()),
            framerate=int(self._fps.get_value()),
        )
        input_cfg = replace(
            self._config.input,
            prefer_wired_adb=self._prefer_wired.get_active(),
            click_to_control=self._auto_kbd.get_active(),
            release_on_unfocus=self._release_unfocus.get_active(),
            release_on_escape=self._release_esc.get_active(),
            scroll_threshold=self._scroll.get_value(),
            soft_buttons_work_unfocused=self._soft_unfocused.get_active(),
            default_pointer_mode="nav" if self._default_mode.get_selected() == 0 else "mouse",
        )
        window = replace(
            self._config.window,
            remember_geometry=self._remember_geo.get_active(),
            aspect_ratio_locked=self._aspect.get_active(),
            chrome_auto_hide=self._chrome_hide.get_active(),
            chrome_hide_delay_ms=int(self._chrome_delay.get_value()),
            banner_auto_hide_ms=int(self._banner_hide.get_value()),
            control_bar_collapsed=self._bar_collapsed.get_active(),
            pip=replace(
                self._config.window.pip,
                soft_bar_auto_hide=self._pip_hide.get_active(),
                opacity=self._pip_opacity.get_value(),
            ),
        )
        updated = replace(
            self._config,
            adb=adb,
            capture=capture,
            input=input_cfg,
            shortcuts=shortcuts,
            window=window,
            watch_autostart_enabled=self._watch_enable.get_active(),
            watch_poll_interval_s=self._watch_poll.get_value(),
        )
        save_config(updated)
        self._on_saved(updated)
        self.close()
