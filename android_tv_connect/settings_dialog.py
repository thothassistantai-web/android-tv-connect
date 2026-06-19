"""Application settings dialog."""

from __future__ import annotations

import threading
from dataclasses import replace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GLib", "2.0")
from gi.repository import Adw, GLib, Gtk

from .adb_discovery import (
    WiredDeviceOption,
    WirelessDeviceOption,
    discover_adb_devices,
)
from .adb_settings import (
    normalize_adb_config,
    normalize_wired_serial,
    normalize_wireless_host,
    parse_wireless_port,
    wireless_host_is_auto,
)
from .branding import APP_NAME, VERSION
from .config import AdbConfig, AppConfig, ScrcpyConfig
from .settings_store import save_config
from .shortcuts import SHORTCUT_DEFINITIONS, SHORTCUT_HELP, validate_shortcut
from .update_ui import check_for_updates

_AUTO_LABEL = "Auto (first available)"
_MANUAL_LABEL = "Manual…"


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
        page.add(self._scrcpy_group())
        page.add(self._capture_group())
        page.add(self._input_group())
        page.add(self._shortcuts_group())
        page.add(self._window_group())
        page.add(self._updates_group())
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

    def _show_info(self, heading: str, body: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=heading,
            body=body,
        )
        dialog.add_response("ok", "OK")
        dialog.present()

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
        group.set_description(
            "Pick a discovered device or choose Manual for a custom serial or IP."
        )

        self._wired_devices: list[WiredDeviceOption] = []
        self._wireless_devices: list[WirelessDeviceOption] = []

        self._wired_combo = Adw.ComboRow(title="Wired device")
        self._wired_combo.connect("notify::selected", self._on_wired_selection_changed)
        group.add(self._wired_combo)

        self._wired_manual = self._entry_row(
            "Wired serial (manual)",
            "USB serial from adb devices -l",
            self._config.adb.wired_serial or "",
        )
        self._wired_manual.set_visible(False)
        group.add(self._wired_manual)

        self._wireless_combo = Adw.ComboRow(title="Wireless device")
        self._wireless_combo.connect("notify::selected", self._on_wireless_selection_changed)
        group.add(self._wireless_combo)

        self._wireless_manual = self._entry_row(
            "Wireless host (manual)",
            "IP or hostname when USB is unavailable",
            self._config.adb.wireless_host,
        )
        self._wireless_manual.set_visible(False)
        group.add(self._wireless_manual)

        self._wireless_port = self._entry_row(
            "Wireless port", "TCP port for network ADB", str(self._config.adb.wireless_port)
        )
        group.add(self._wireless_port)

        refresh_row = Adw.ActionRow(title="Refresh devices")
        refresh_row.set_subtitle("Re-scan adb devices and the local network")
        self._refresh_button = Gtk.Button(label="Refresh")
        self._refresh_button.connect("clicked", self._on_refresh_devices)
        refresh_row.add_suffix(self._refresh_button)
        group.add(refresh_row)

        self._prefer_wired = self._switch_row(
            "Prefer wired ADB",
            "Use USB debugging before attempting wireless connect",
            self._config.input.prefer_wired_adb,
        )
        group.add(self._prefer_wired)

        self._refresh_adb_devices(initial=True)
        return group

    def _scrcpy_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Screen mirror (scrcpy)")
        group.set_description(
            "Optional ADB screen mirror window. Uses the same wired or wireless "
            "ADB target as remote control."
        )

        self._scrcpy_auto = self._switch_row(
            "Auto-launch on ADB connect",
            "Open scrcpy when a device connects (off by default)",
            self._config.scrcpy.auto_launch_on_connect,
        )
        group.add(self._scrcpy_auto)

        self._scrcpy_path = self._entry_row(
            "scrcpy path",
            "Leave empty to use scrcpy from PATH",
            self._config.scrcpy.scrcpy_path,
        )
        group.add(self._scrcpy_path)

        self._scrcpy_max_size = self._spin_row(
            "Max size (px)",
            float(self._config.scrcpy.max_size),
            160,
            0,
            3840,
        )
        group.add(self._scrcpy_max_size)

        self._scrcpy_bit_rate = self._entry_row(
            "Video bit rate",
            "scrcpy --video-bit-rate value, e.g. 8M or 2M",
            self._config.scrcpy.bit_rate,
        )
        group.add(self._scrcpy_bit_rate)

        self._scrcpy_title = self._entry_row(
            "Window title",
            "Title for the scrcpy window",
            self._config.scrcpy.window_title,
        )
        group.add(self._scrcpy_title)

        self._scrcpy_fullscreen = self._switch_row(
            "Start fullscreen",
            "Launch scrcpy in fullscreen mode",
            self._config.scrcpy.fullscreen,
        )
        group.add(self._scrcpy_fullscreen)

        self._scrcpy_no_audio = self._switch_row(
            "No audio",
            "Disable audio forwarding (recommended for TV sticks)",
            self._config.scrcpy.no_audio,
        )
        group.add(self._scrcpy_no_audio)

        self._scrcpy_stay_awake = self._switch_row(
            "Stay awake",
            "Keep the device awake while mirroring",
            self._config.scrcpy.stay_awake,
        )
        group.add(self._scrcpy_stay_awake)

        self._scrcpy_turn_off = self._switch_row(
            "Turn screen off",
            "Turn device screen off while mirroring (may not work on all TVs)",
            self._config.scrcpy.turn_screen_off,
        )
        group.add(self._scrcpy_turn_off)

        return group

    def _wired_combo_labels(self) -> list[str]:
        labels = [_AUTO_LABEL]
        labels.extend(device.description for device in self._wired_devices)
        labels.append(_MANUAL_LABEL)
        return labels

    def _wireless_combo_labels(self) -> list[str]:
        labels = [_AUTO_LABEL]
        labels.extend(device.description for device in self._wireless_devices)
        labels.append(_MANUAL_LABEL)
        return labels

    def _set_combo_model(self, combo: Adw.ComboRow, labels: list[str]) -> None:
        combo.set_model(Gtk.StringList.new(labels))

    def _wired_manual_index(self) -> int:
        return len(self._wired_devices) + 1

    def _wireless_manual_index(self) -> int:
        return len(self._wireless_devices) + 1

    def _select_wired_from_config(self) -> None:
        serial = self._config.adb.wired_serial
        if not serial:
            self._wired_combo.set_selected(0)
            self._wired_manual.set_visible(False)
            return

        for index, device in enumerate(self._wired_devices, start=1):
            if device.serial == serial:
                self._wired_combo.set_selected(index)
                self._wired_manual.set_visible(False)
                return

        self._wired_combo.set_selected(self._wired_manual_index())
        self._wired_manual.set_text(serial)
        self._wired_manual.set_visible(True)

    def _select_wireless_from_config(self) -> None:
        host = self._config.adb.wireless_host
        port = self._config.adb.wireless_port
        if wireless_host_is_auto(host):
            self._wireless_combo.set_selected(0)
            self._wireless_manual.set_visible(False)
            return

        for index, device in enumerate(self._wireless_devices, start=1):
            if device.host == host and device.port == port:
                self._wireless_combo.set_selected(index)
                self._wireless_manual.set_visible(False)
                self._wireless_port.set_text(str(device.port))
                return

        self._wireless_combo.set_selected(self._wireless_manual_index())
        self._wireless_manual.set_text(host)
        self._wireless_manual.set_visible(True)

    def _on_wired_selection_changed(self, *_args) -> None:
        manual = self._wired_combo.get_selected() == self._wired_manual_index()
        self._wired_manual.set_visible(manual)

    def _on_wireless_selection_changed(self, *_args) -> None:
        selected = self._wireless_combo.get_selected()
        manual = selected == self._wireless_manual_index()
        self._wireless_manual.set_visible(manual)
        if manual or selected <= 0:
            return
        device = self._wireless_devices[selected - 1]
        self._wireless_port.set_text(str(device.port))

    def _current_wireless_port(self) -> int:
        port, _err = parse_wireless_port(self._wireless_port.get_text())
        return port or AdbConfig.wireless_port

    def _refresh_adb_devices(self, *, initial: bool = False) -> None:
        if not initial:
            self._refresh_button.set_sensitive(False)
            self._refresh_button.set_label("Scanning…")

        port = self._current_wireless_port() if not initial else self._config.adb.wireless_port

        def work() -> None:
            try:
                wired, wireless = discover_adb_devices(
                    scan_subnet=not initial,
                    wireless_port=port,
                )
            except Exception:
                wired, wireless = [], []
            GLib.idle_add(self._apply_discovered_devices, wired, wireless, initial)

        if initial:
            try:
                wired, wireless = discover_adb_devices(scan_subnet=False, wireless_port=port)
            except Exception:
                wired, wireless = [], []
            self._apply_discovered_devices(wired, wireless, True)
            return

        threading.Thread(target=work, daemon=True, name="adb-device-scan").start()

    def _apply_discovered_devices(
        self,
        wired: list[WiredDeviceOption],
        wireless: list[WirelessDeviceOption],
        initial: bool,
    ) -> bool:
        self._wired_devices = wired
        self._wireless_devices = wireless
        self._set_combo_model(self._wired_combo, self._wired_combo_labels())
        self._set_combo_model(self._wireless_combo, self._wireless_combo_labels())
        self._select_wired_from_config()
        self._select_wireless_from_config()
        if not initial:
            self._refresh_button.set_sensitive(True)
            self._refresh_button.set_label("Refresh")
        return False

    def _on_refresh_devices(self, *_args) -> None:
        self._refresh_adb_devices(initial=False)

    def _wired_serial_value(self) -> str:
        selected = self._wired_combo.get_selected()
        if selected <= 0:
            return ""
        if selected == self._wired_manual_index():
            return self._wired_manual.get_text().strip()
        return self._wired_devices[selected - 1].serial

    def _wireless_host_value(self) -> str:
        selected = self._wireless_combo.get_selected()
        if selected <= 0:
            return ""
        if selected == self._wireless_manual_index():
            return self._wireless_manual.get_text().strip()
        return self._wireless_devices[selected - 1].host

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

    def _updates_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Updates")
        group.set_description(
            "The launcher checks GitHub releases before starting the app. "
            "If the main app breaks, run atv-connect to recover."
        )

        self._auto_check_updates = self._switch_row(
            "Check on launch",
            "Let the launcher look for updates when you start the app",
            self._config.updates.auto_check_on_launch,
        )
        group.add(self._auto_check_updates)

        self._manifest_override = self._entry_row(
            "Manifest URL override",
            "Leave empty to use the default GitHub releases endpoint",
            self._config.updates.manifest_url_override,
        )
        group.add(self._manifest_override)

        notes_text = self._config.updates.last_release_notes.strip()
        self._release_notes_row = Adw.ActionRow(
            title="Latest release notes",
            subtitle=notes_text or "Run “Check for updates now” to fetch notes",
        )
        self._release_notes_row.set_activatable(False)
        group.add(self._release_notes_row)

        check_row = Adw.ActionRow(title="Check for updates now")
        check_row.set_subtitle("Uses the isolated launcher updater")
        self._check_updates_button = Gtk.Button(label="Check")
        self._check_updates_button.connect("clicked", self._on_check_updates)
        check_row.add_suffix(self._check_updates_button)
        group.add(check_row)

        return group

    def _on_check_updates(self, *_args) -> None:
        self._check_updates_button.set_sensitive(False)
        self._check_updates_button.set_label("Checking…")

        def work() -> None:
            result = check_for_updates(apply=False)
            GLib.idle_add(self._show_update_result, result)

        threading.Thread(target=work, daemon=True, name="update-check").start()

    def _show_update_result(self, result) -> bool:
        self._check_updates_button.set_sensitive(True)
        self._check_updates_button.set_label("Check")

        if not result.ok and result.error:
            self._show_error("Update check failed", result.error)
            return False

        if result.release_notes:
            self._release_notes_row.set_subtitle(result.release_notes[:500])

        if result.update_available:
            version = result.manifest_version or "unknown"
            body = (
                f"Version {version} is available "
                f"(installed {result.installed_version}).\n\n"
                f"{result.release_notes or 'Restart the app to install via the launcher.'}"
            )
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Update available",
                body=body,
            )
            dialog.add_response("later", "Later")
            dialog.add_response("install", "Install and restart")
            dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)

            def on_response(_dialog, response: str) -> None:
                if response != "install":
                    return
                self._check_updates_button.set_sensitive(False)
                self._check_updates_button.set_label("Installing…")

                def install_work() -> None:
                    install_result = check_for_updates(apply=True)
                    GLib.idle_add(self._finish_install, install_result)

                threading.Thread(
                    target=install_work,
                    daemon=True,
                    name="update-install",
                ).start()

            dialog.connect("response", on_response)
            dialog.present()
            return False

        self._show_info(
            "Up to date",
            f"You are running {result.installed_version} (versionCode {result.installed_version_code}).",
        )
        return False

    def _finish_install(self, result) -> bool:
        self._check_updates_button.set_sensitive(True)
        self._check_updates_button.set_label("Check")
        if result.error:
            self._show_error("Install failed", result.error)
            return False
        if result.release_notes:
            self._release_notes_row.set_subtitle(result.release_notes[:500])
        self._show_info(
            "Update installed",
            "Quit and launch atv-connect again to run the new version.",
        )
        return False

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

        wireless_host_raw = self._wireless_host_value()
        port, port_err = parse_wireless_port(self._wireless_port.get_text())
        if port_err:
            self._show_error("Invalid wireless port", port_err)
            return

        adb = replace(
            self._config.adb,
            wired_serial=normalize_wired_serial(self._wired_serial_value()),
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
        scrcpy = replace(
            self._config.scrcpy,
            auto_launch_on_connect=self._scrcpy_auto.get_active(),
            scrcpy_path=self._scrcpy_path.get_text().strip(),
            max_size=int(self._scrcpy_max_size.get_value()),
            bit_rate=self._scrcpy_bit_rate.get_text().strip() or ScrcpyConfig.bit_rate,
            fullscreen=self._scrcpy_fullscreen.get_active(),
            no_audio=self._scrcpy_no_audio.get_active(),
            stay_awake=self._scrcpy_stay_awake.get_active(),
            turn_screen_off=self._scrcpy_turn_off.get_active(),
            window_title=self._scrcpy_title.get_text().strip() or ScrcpyConfig.window_title,
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
            scrcpy=scrcpy,
            input=input_cfg,
            shortcuts=shortcuts,
            window=window,
            updates=replace(
                self._config.updates,
                auto_check_on_launch=self._auto_check_updates.get_active(),
                manifest_url_override=self._manifest_override.get_text().strip(),
                last_release_notes=self._release_notes_row.get_subtitle() or "",
            ),
            watch_autostart_enabled=self._watch_enable.get_active(),
            watch_poll_interval_s=self._watch_poll.get_value(),
        )
        save_config(updated)
        self._on_saved(updated)
        self.close()
