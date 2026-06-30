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
from .audio_source_test import (
    AudioTestSource,
    build_audio_test_queue,
    next_queue_index,
)
from .branding import APP_NAME, VERSION
from .capture_device import list_viable_audio_sources, resolve_audio_device
from .config import AppConfig, CaptureConfig, ScrcpyConfig
from .media_enumeration import (
    AudioSourceOption,
    VideoDeviceOption,
    enumerate_audio_sources,
    enumerate_v4l2_devices,
)
from .settings_presets import (
    BIT_RATE_PRESETS,
    FRAMERATE_PRESETS,
    MAX_SIZE_PRESETS,
    RESOLUTION_PRESETS,
    WIRELESS_PORT_PRESETS,
    bit_rate_index,
    framerate_index,
    max_size_index,
    normalize_capture_config,
    normalize_scrcpy_config,
    resolution_index,
    validate_adb_for_save,
    wireless_port_preset_index,
)
from .settings_draft import config_snapshot, configs_differ
from .shortcuts import SHORTCUT_DEFINITIONS, SHORTCUT_HELP, validate_shortcut
from .update_ui import check_for_updates

_AUTO_WIRED_LABEL = "Auto (first USB)"
_AUTO_WIRELESS_LABEL = "Auto (first wireless)"
_AUTO_VIDEO_LABEL = "Auto (discover dongle)"
_AUTO_AUDIO_LABEL = "Auto (HDMI capture dongle)"
_MANUAL_LABEL = "Manual…"
_CUSTOM_PORT_LABEL = "Custom…"
_SEAMLESS_DOCS = "docs/SEAMLESS-UX.md"
_LIVE_APPLY_DEBOUNCE_MS = 300
_AUDIO_CONFIRM_DELAY_MS = 900
_AUDIO_TEST_WARMUP_MS = 1200


class SettingsDialog(Adw.Window):
    def __init__(
        self,
        parent: Gtk.Window,
        config: AppConfig,
        host,
        on_saved,
    ) -> None:
        super().__init__(transient_for=parent, modal=True)
        self._host = host
        self._saved_config = config_snapshot(config)
        self._on_saved = on_saved
        self._closing_saved = False
        self._closed = False
        self._suppress_live_apply = 0
        self._live_apply_timer: int | None = None
        self._audio_confirm_timer: int | None = None
        self._audio_confirm_cycling = False
        self._audio_confirm_dialog: Adw.MessageDialog | None = None
        self._audio_line_test_active = False
        self._audio_line_test_queue: list[AudioTestSource] = []
        self._audio_line_test_index = 0
        self.set_title("Android TV Connect Settings")
        self.set_default_size(520, 640)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self._title_widget = Adw.WindowTitle(
            title="Settings", subtitle="Android TV Connect"
        )
        header.set_title_widget(self._title_widget)
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
        self._install_escape_close()
        self._wire_live_apply_handlers()

    def _install_escape_close(self) -> None:
        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.LOCAL)
        controller.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.CallbackAction.new(self._on_escape_shortcut),
            )
        )
        self.add_controller(controller)

    def _on_escape_shortcut(self, *_args) -> bool:
        self._revert_and_close()
        return True

    def _cancel_settings_async_ui(self) -> None:
        self._cancel_live_apply_timer()
        self._cancel_audio_confirm_timer()
        self._dismiss_audio_confirm_dialog()
        self._stop_audio_line_test()

    def _on_close_request(self, *_args) -> bool:
        if self._closing_saved or self._closed:
            return False
        self._closed = True
        self._cancel_settings_async_ui()
        self._host.cancel_settings_preview()
        self._host.schedule_settings_revert(self._saved_config)
        return False

    def _revert_and_close(self) -> None:
        if self._closed:
            return
        saved = self._saved_config
        self._closed = True
        self._cancel_settings_async_ui()
        self._host.cancel_settings_preview()
        self.close()
        self._host.schedule_settings_revert(saved)

    def _on_cancel(self, *_args) -> None:
        self._revert_and_close()

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
            "Choose a discovered device or Manual for a custom serial or IP. "
            "Leave on Auto to connect to the first available device. "
            "HDMI capture and ADB are independent — video uses the capture card "
            "while remote control uses ADB."
        )

        self._wired_devices: list[WiredDeviceOption] = []
        self._wireless_devices: list[WirelessDeviceOption] = []

        self._wired_combo = Adw.ComboRow(title="Wired device")
        self._wired_combo.connect("notify::selected", self._on_wired_selection_changed)
        group.add(self._wired_combo)

        self._wired_manual = self._entry_row(
            "Wired serial (manual)",
            "USB serial from adb devices -l",
            self._saved_config.adb.wired_serial or "",
        )
        self._wired_manual.set_visible(False)
        group.add(self._wired_manual)

        self._wireless_combo = Adw.ComboRow(title="Wireless device")
        self._wireless_combo.connect("notify::selected", self._on_wireless_selection_changed)
        group.add(self._wireless_combo)

        self._wireless_manual = self._entry_row(
            "Wireless host (manual)",
            "IP or hostname when USB is unavailable",
            self._saved_config.adb.wireless_host,
        )
        self._wireless_manual.set_visible(False)
        group.add(self._wireless_manual)

        self._wireless_port_combo = Adw.ComboRow(title="Wireless port")
        port_labels = [str(port) for port in WIRELESS_PORT_PRESETS] + [_CUSTOM_PORT_LABEL]
        self._wireless_port_combo.set_model(Gtk.StringList.new(port_labels))
        self._wireless_port_combo.connect(
            "notify::selected", self._on_wireless_port_selection_changed
        )
        group.add(self._wireless_port_combo)

        self._wireless_port_custom = self._spin_row(
            "Custom wireless port", float(self._saved_config.adb.wireless_port), 1, 1024, 65535
        )
        self._wireless_port_custom.set_digits(0)
        self._wireless_port_custom.set_visible(False)
        group.add(self._wireless_port_custom)

        refresh_row = Adw.ActionRow(title="Refresh devices")
        refresh_row.set_subtitle("Re-scan adb devices and the local network")
        self._refresh_button = Gtk.Button(label="Refresh")
        self._refresh_button.connect("clicked", self._on_refresh_devices)
        refresh_row.add_suffix(self._refresh_button)
        group.add(refresh_row)

        self._prefer_wired = self._switch_row(
            "Prefer wired ADB",
            "Use USB debugging before attempting wireless connect",
            self._saved_config.input.prefer_wired_adb,
        )
        group.add(self._prefer_wired)

        self._select_wireless_port_from_config()
        self._refresh_adb_devices(initial=True)
        return group

    def _scrcpy_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Screen mirror (scrcpy)")
        group.set_description(
            "Optional ADB screen mirror window. Uses the same wired or wireless "
            "ADB target as remote control. Option changes apply on the next mirror "
            "launch (a running mirror is not restarted)."
        )

        self._scrcpy_auto = self._switch_row(
            "Auto-launch on ADB connect",
            "Open scrcpy when a device connects (off by default)",
            self._saved_config.scrcpy.auto_launch_on_connect,
        )
        group.add(self._scrcpy_auto)

        self._scrcpy_bit_rate = Adw.ComboRow(title="Video bit rate")
        self._scrcpy_bit_rate.set_model(Gtk.StringList.new(list(BIT_RATE_PRESETS)))
        self._scrcpy_bit_rate.set_selected(bit_rate_index(self._saved_config.scrcpy.bit_rate))
        group.add(self._scrcpy_bit_rate)

        max_labels = [label for label, _value in MAX_SIZE_PRESETS]
        self._scrcpy_max_size = Adw.ComboRow(title="Max size")
        self._scrcpy_max_size.set_model(Gtk.StringList.new(max_labels))
        self._scrcpy_max_size.set_selected(max_size_index(self._saved_config.scrcpy.max_size))
        group.add(self._scrcpy_max_size)

        self._scrcpy_fullscreen = self._switch_row(
            "Start fullscreen",
            "Launch scrcpy in fullscreen mode",
            self._saved_config.scrcpy.fullscreen,
        )
        group.add(self._scrcpy_fullscreen)

        self._scrcpy_no_audio = self._switch_row(
            "No audio",
            "Disable audio forwarding (recommended for TV sticks)",
            self._saved_config.scrcpy.no_audio,
        )
        group.add(self._scrcpy_no_audio)

        self._scrcpy_stay_awake = self._switch_row(
            "Stay awake",
            "Keep the device awake while mirroring",
            self._saved_config.scrcpy.stay_awake,
        )
        group.add(self._scrcpy_stay_awake)

        self._scrcpy_turn_off = self._switch_row(
            "Turn screen off",
            "Turn device screen off while mirroring (may not work on all TVs)",
            self._saved_config.scrcpy.turn_screen_off,
        )
        group.add(self._scrcpy_turn_off)

        advanced = Adw.ExpanderRow(title="Advanced scrcpy options")
        advanced.set_subtitle("Custom binary path and window title")

        self._scrcpy_path = self._entry_row(
            "scrcpy path",
            "Leave empty to use scrcpy from PATH",
            self._saved_config.scrcpy.scrcpy_path,
        )
        self._scrcpy_title = self._entry_row(
            "Window title",
            "Title for the scrcpy window",
            self._saved_config.scrcpy.window_title,
        )
        advanced.add_row(self._scrcpy_path)
        advanced.add_row(self._scrcpy_title)
        group.add(advanced)

        return group

    def _wired_combo_labels(self) -> list[str]:
        labels = [_AUTO_WIRED_LABEL]
        labels.extend(device.description for device in self._wired_devices)
        labels.append(_MANUAL_LABEL)
        return labels

    def _wireless_combo_labels(self) -> list[str]:
        labels = [_AUTO_WIRELESS_LABEL]
        labels.extend(device.description for device in self._wireless_devices)
        labels.append(_MANUAL_LABEL)
        return labels

    def _set_combo_model(self, combo: Adw.ComboRow, labels: list[str]) -> None:
        combo.set_model(Gtk.StringList.new(labels))

    def _wired_manual_index(self) -> int:
        return len(self._wired_devices) + 1

    def _wireless_manual_index(self) -> int:
        return len(self._wireless_devices) + 1

    def _wired_serial_for_selection(self) -> str:
        if self._suppress_live_apply > 0:
            return self._saved_config.adb.wired_serial
        return normalize_wired_serial(self._wired_serial_value())

    def _wireless_host_port_for_selection(self) -> tuple[str, int]:
        if self._suppress_live_apply > 0:
            return self._saved_config.adb.wireless_host, self._saved_config.adb.wireless_port
        port = self._current_wireless_port()
        parsed, err = parse_wireless_port(str(port))
        if err or parsed is None:
            parsed = self._saved_config.adb.wireless_port
        return normalize_wireless_host(self._wireless_host_value()), parsed

    def _video_device_for_selection(self) -> str:
        if self._suppress_live_apply > 0:
            return self._saved_config.capture.video_device
        return self._video_device_value()

    def _audio_device_for_selection(self) -> str:
        if self._suppress_live_apply > 0:
            return self._saved_config.capture.audio_device
        return self._audio_device_value()

    def _select_wired_from_config(self) -> None:
        serial = self._wired_serial_for_selection()
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
        host, port = self._wireless_host_port_for_selection()
        if wireless_host_is_auto(host):
            self._wireless_combo.set_selected(0)
            self._wireless_manual.set_visible(False)
            return

        for index, device in enumerate(self._wireless_devices, start=1):
            if device.host == host and device.port == port:
                self._wireless_combo.set_selected(index)
                self._wireless_manual.set_visible(False)
                self._select_wireless_port(port)
                return

        self._wireless_combo.set_selected(self._wireless_manual_index())
        self._wireless_manual.set_text(host)
        self._wireless_manual.set_visible(True)
        self._select_wireless_port(port)

    def _select_wireless_port(self, port: int) -> None:
        index = wireless_port_preset_index(port)
        self._wireless_port_combo.set_selected(index)
        custom = index == len(WIRELESS_PORT_PRESETS)
        self._wireless_port_custom.set_visible(custom)
        if custom:
            self._wireless_port_custom.set_value(float(port))

    def _select_wireless_port_from_config(self) -> None:
        self._select_wireless_port(self._saved_config.adb.wireless_port)

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
        self._select_wireless_port(device.port)

    def _on_wireless_port_selection_changed(self, *_args) -> None:
        custom = self._wireless_port_combo.get_selected() == len(WIRELESS_PORT_PRESETS)
        self._wireless_port_custom.set_visible(custom)

    def _current_wireless_port(self) -> int:
        selected = self._wireless_port_combo.get_selected()
        if selected < len(WIRELESS_PORT_PRESETS):
            return WIRELESS_PORT_PRESETS[selected]
        return int(self._wireless_port_custom.get_value())

    def _refresh_adb_devices(self, *, initial: bool = False) -> None:
        self._refresh_button.set_sensitive(False)
        self._refresh_button.set_label("Scanning…" if not initial else "Loading…")
        self._wired_combo.set_sensitive(False)
        self._wireless_combo.set_sensitive(False)

        port = self._current_wireless_port() if not initial else self._saved_config.adb.wireless_port

        def work() -> None:
            try:
                wired, wireless = discover_adb_devices(
                    scan_subnet=not initial,
                    wireless_port=port,
                )
            except Exception:
                wired, wireless = [], []
            GLib.idle_add(self._apply_discovered_devices, wired, wireless, initial)

        threading.Thread(target=work, daemon=True, name="adb-device-scan").start()

    def _apply_discovered_devices(
        self,
        wired: list[WiredDeviceOption],
        wireless: list[WirelessDeviceOption],
        initial: bool,
    ) -> bool:
        if self._closed:
            return False
        self._suppress_live_apply += 1
        try:
            self._wired_devices = wired
            self._wireless_devices = wireless
            self._set_combo_model(self._wired_combo, self._wired_combo_labels())
            self._set_combo_model(self._wireless_combo, self._wireless_combo_labels())
            self._select_wired_from_config()
            self._select_wireless_from_config()
            self._wired_combo.set_sensitive(True)
            self._wireless_combo.set_sensitive(True)
            self._refresh_button.set_sensitive(True)
            self._refresh_button.set_label("Refresh")
        finally:
            self._suppress_live_apply -= 1
        self._update_unsaved_indicator()
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
        group.set_description(
            "HDMI capture and ADB are independent — video comes from the capture "
            f"card while remote control uses ADB. See {_SEAMLESS_DOCS} for the "
            "connection strategy."
        )

        self._video_devices: list[VideoDeviceOption] = []
        self._audio_sources: list[AudioSourceOption] = []

        self._video_combo = Adw.ComboRow(title="Video device")
        self._video_combo.connect("notify::selected", self._on_video_selection_changed)
        group.add(self._video_combo)

        self._video_manual = self._entry_row(
            "Video device (manual)",
            "V4L2 node such as /dev/video1",
            self._saved_config.capture.video_device,
        )
        self._video_manual.set_visible(False)
        group.add(self._video_manual)

        resolution_labels = [label for label, _w, _h in RESOLUTION_PRESETS]
        self._resolution_combo = Adw.ComboRow(title="Resolution")
        self._resolution_combo.set_model(Gtk.StringList.new(resolution_labels))
        self._resolution_combo.set_selected(
            resolution_index(self._saved_config.capture.width, self._saved_config.capture.height)
        )
        group.add(self._resolution_combo)

        self._fps_combo = Adw.ComboRow(title="Framerate")
        self._fps_combo.set_model(
            Gtk.StringList.new([f"{value} fps" for value in FRAMERATE_PRESETS])
        )
        self._fps_combo.set_selected(framerate_index(self._saved_config.capture.framerate))
        group.add(self._fps_combo)

        self._audio_combo = Adw.ComboRow(title="Audio device")
        self._audio_combo.connect("notify::selected", self._on_audio_selection_changed)
        group.add(self._audio_combo)

        self._audio_manual = self._entry_row(
            "Audio device (manual)",
            "PipeWire or PulseAudio source name",
            self._saved_config.capture.audio_device,
        )
        self._audio_manual.set_visible(False)
        group.add(self._audio_manual)

        test_row = Adw.ActionRow(title="Test audio sources")
        test_row.set_subtitle(
            "Try each input one by one. You confirm before the next source is tried."
        )
        self._audio_test_button = Gtk.Button(label="Start test")
        self._audio_test_button.connect("clicked", self._on_start_audio_line_test)
        test_row.add_suffix(self._audio_test_button)
        group.add(test_row)

        refresh_row = Adw.ActionRow(title="Refresh capture devices")
        refresh_row.set_subtitle("Re-scan V4L2 nodes and audio sources")
        self._capture_refresh_button = Gtk.Button(label="Refresh")
        self._capture_refresh_button.connect("clicked", self._on_refresh_capture_devices)
        refresh_row.add_suffix(self._capture_refresh_button)
        group.add(refresh_row)

        self._refresh_capture_devices(initial=True)
        return group

    def _video_combo_labels(self) -> list[str]:
        labels = [_AUTO_VIDEO_LABEL]
        labels.extend(device.description for device in self._video_devices)
        labels.append(_MANUAL_LABEL)
        return labels

    def _audio_combo_labels(self) -> list[str]:
        labels = [_AUTO_AUDIO_LABEL]
        labels.extend(
            f"{source.description} ({source.name})" for source in self._audio_sources
        )
        labels.append(_MANUAL_LABEL)
        return labels

    def _video_manual_index(self) -> int:
        return len(self._video_devices) + 1

    def _audio_manual_index(self) -> int:
        return len(self._audio_sources) + 1

    def _select_video_from_config(self) -> None:
        configured = (self._video_device_for_selection() or "").strip()
        if not configured or configured.lower() == "auto":
            self._video_combo.set_selected(0)
            self._video_manual.set_visible(False)
            return
        for index, device in enumerate(self._video_devices, start=1):
            if device.node == configured:
                self._video_combo.set_selected(index)
                self._video_manual.set_visible(False)
                return
        self._video_combo.set_selected(self._video_manual_index())
        self._video_manual.set_text(configured)
        self._video_manual.set_visible(True)

    def _select_audio_from_config(self) -> None:
        configured = (self._audio_device_for_selection() or "").strip()
        if not configured or configured.lower() == "auto":
            self._audio_combo.set_selected(0)
            self._audio_manual.set_visible(False)
            return
        for index, source in enumerate(self._audio_sources, start=1):
            if source.name == configured:
                self._audio_combo.set_selected(index)
                self._audio_manual.set_visible(False)
                return
        self._audio_combo.set_selected(self._audio_manual_index())
        self._audio_manual.set_text(configured)
        self._audio_manual.set_visible(True)

    def _on_video_selection_changed(self, *_args) -> None:
        manual = self._video_combo.get_selected() == self._video_manual_index()
        self._video_manual.set_visible(manual)

    def _on_audio_selection_changed(self, *_args) -> None:
        manual = self._audio_combo.get_selected() == self._audio_manual_index()
        self._audio_manual.set_visible(manual)
        if self._suppress_live_apply > 0 or self._audio_confirm_cycling:
            return
        if self._audio_line_test_active:
            self._stop_audio_line_test()
        self._schedule_audio_confirm()

    def _cancel_audio_confirm_timer(self) -> None:
        if self._audio_confirm_timer is not None:
            GLib.source_remove(self._audio_confirm_timer)
            self._audio_confirm_timer = None

    def _dismiss_audio_confirm_dialog(self) -> None:
        if self._audio_confirm_dialog is not None:
            self._audio_confirm_dialog.close()
            self._audio_confirm_dialog = None

    def _stop_audio_line_test(self) -> None:
        self._audio_line_test_active = False
        self._audio_line_test_queue = []
        self._audio_line_test_index = 0
        if hasattr(self, "_audio_test_button"):
            self._audio_test_button.set_sensitive(True)
            self._audio_test_button.set_label("Start test")

    def _capture_config_for_audio_resolve(self) -> CaptureConfig:
        width, height = self._resolution_value()
        return CaptureConfig(
            video_device=self._video_device_value(),
            audio_device=self._audio_device_value(),
            width=width,
            height=height,
            framerate=self._current_framerate(),
        )

    def _current_audio_test_source_fast(self) -> AudioTestSource | None:
        """Resolve the current audio test source without subprocess I/O."""
        selected = self._audio_combo.get_selected()
        if selected == self._audio_manual_index():
            manual = self._audio_manual.get_text().strip()
            if not manual:
                return None
            return AudioTestSource(name=manual, label=manual)
        if selected > 0:
            source = self._audio_sources[selected - 1]
            return AudioTestSource(
                name=source.name,
                label=source.description or source.name,
            )
        return None

    def _current_audio_test_source(self) -> AudioTestSource | None:
        selected = self._audio_combo.get_selected()
        if selected <= 0:
            resolved = resolve_audio_device(self._capture_config_for_audio_resolve())
            if not resolved:
                return None
            return AudioTestSource(name=resolved, label=_AUTO_AUDIO_LABEL)
        return self._current_audio_test_source_fast()

    def _build_audio_test_queue_sync(self) -> list[AudioTestSource]:
        manual = ""
        if self._audio_combo.get_selected() == self._audio_manual_index():
            manual = self._audio_manual.get_text().strip()
        auto_resolved = resolve_audio_device(
            replace(self._capture_config_for_audio_resolve(), audio_device="auto")
        )
        return build_audio_test_queue(
            list_viable_audio_sources(self._audio_sources),
            manual_name=manual,
            include_auto_resolved=auto_resolved,
            auto_label=_AUTO_AUDIO_LABEL,
        )

    def _audio_test_queue_for_settings(self) -> list[AudioTestSource]:
        return self._build_audio_test_queue_sync()

    def _resolve_audio_test_source_async(
        self,
        on_ready,
        *,
        thread_name: str = "audio-resolve",
    ) -> None:
        capture_cfg = self._capture_config_for_audio_resolve()

        def work() -> None:
            resolved = resolve_audio_device(capture_cfg)
            source = (
                AudioTestSource(name=resolved, label=_AUTO_AUDIO_LABEL)
                if resolved
                else None
            )
            GLib.idle_add(on_ready, source)

        threading.Thread(target=work, daemon=True, name=thread_name).start()

    def _build_audio_test_queue_async(self, on_ready) -> None:
        capture_cfg = self._capture_config_for_audio_resolve()
        manual = ""
        if self._audio_combo.get_selected() == self._audio_manual_index():
            manual = self._audio_manual.get_text().strip()
        sources = list(self._audio_sources)

        def work() -> None:
            auto_resolved = resolve_audio_device(
                replace(capture_cfg, audio_device="auto")
            )
            queue = build_audio_test_queue(
                list_viable_audio_sources(sources),
                manual_name=manual,
                include_auto_resolved=auto_resolved,
                auto_label=_AUTO_AUDIO_LABEL,
            )
            GLib.idle_add(on_ready, queue)

        threading.Thread(
            target=work,
            daemon=True,
            name="audio-test-queue",
        ).start()

    def _current_framerate(self) -> int:
        selected = self._fps_combo.get_selected()
        if 0 <= selected < len(FRAMERATE_PRESETS):
            return FRAMERATE_PRESETS[selected]
        return self._saved_config.capture.framerate

    def _select_audio_source_name(self, name: str) -> None:
        self._audio_confirm_cycling = True
        self._suppress_live_apply += 1
        try:
            for index, source in enumerate(self._audio_sources, start=1):
                if source.name == name:
                    self._audio_combo.set_selected(index)
                    self._audio_manual.set_visible(False)
                    return
            self._audio_combo.set_selected(self._audio_manual_index())
            self._audio_manual.set_text(name)
            self._audio_manual.set_visible(True)
        finally:
            self._suppress_live_apply -= 1
            self._audio_confirm_cycling = False

    def _apply_audio_source_for_test(self, source: AudioTestSource) -> None:
        self._select_audio_source_name(source.name)
        self._schedule_live_apply()
        self._update_unsaved_indicator()

    def _schedule_audio_confirm(self, *, warmup_ms: int | None = None) -> None:
        self._cancel_audio_confirm_timer()
        delay = warmup_ms if warmup_ms is not None else _AUDIO_CONFIRM_DELAY_MS
        self._audio_confirm_timer = GLib.timeout_add(delay, self._prompt_audio_confirm)

    def _on_start_audio_line_test(self, *_args) -> None:
        self._cancel_audio_confirm_timer()
        self._dismiss_audio_confirm_dialog()

        def on_queue_ready(queue: list[AudioTestSource]) -> bool:
            if self._closed:
                return False
            if not queue:
                self._show_info(
                    "No audio sources",
                    "No PipeWire or PulseAudio input sources were found. "
                    "Click Refresh capture devices and try again.",
                )
                return False

            self._audio_line_test_active = True
            self._audio_line_test_queue = queue
            self._audio_line_test_index = 0
            self._audio_test_button.set_sensitive(False)
            self._audio_test_button.set_label("Testing…")
            self._apply_audio_source_for_test(queue[0])
            self._schedule_audio_confirm(warmup_ms=_AUDIO_TEST_WARMUP_MS)
            return False

        self._build_audio_test_queue_async(on_queue_ready)

    def _line_test_progress_text(self) -> str:
        if not self._audio_line_test_active or not self._audio_line_test_queue:
            return ""
        total = len(self._audio_line_test_queue)
        current = min(self._audio_line_test_index + 1, total)
        return f"Source {current} of {total}"

    def _prompt_audio_confirm(self) -> bool:
        self._audio_confirm_timer = None
        if self._closed or self._closing_saved or self._audio_confirm_dialog is not None:
            return False

        if self._audio_line_test_active and self._audio_line_test_queue:
            source = self._audio_line_test_queue[self._audio_line_test_index]
            self._present_audio_confirm_dialog(source)
            return False

        source = self._current_audio_test_source_fast()
        if source is not None:
            self._present_audio_confirm_dialog(source)
            return False

        def on_resolved(resolved: AudioTestSource | None) -> bool:
            if self._closed or self._closing_saved or self._audio_confirm_dialog is not None:
                return False
            if resolved is None:
                self._stop_audio_line_test()
                self._show_info(
                    "Audio source unavailable",
                    "Could not resolve the selected audio source. "
                    "Choose a device from the list or enter a manual source name.",
                )
                return False
            self._present_audio_confirm_dialog(resolved)
            return False

        self._resolve_audio_test_source_async(on_resolved)
        return False

    def _present_audio_confirm_dialog(self, source: AudioTestSource) -> None:
        progress = self._line_test_progress_text()
        progress_line = f"{progress}\n\n" if progress else ""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Can you hear audio?",
            body=(
                f"{progress_line}"
                f"Playback is using “{source.label}”.\n\n"
                "Listen for HDMI capture audio from the TV. "
                "Choose “No” to try the next source, or “Yes” to keep this one."
            ),
        )
        dialog.add_response("next", "No")
        dialog.add_response("yes", "Yes")
        dialog.set_response_appearance("yes", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("yes")
        dialog.set_close_response("yes")

        def on_response(_dlg: Adw.MessageDialog, response: str) -> None:
            self._audio_confirm_dialog = None
            self._on_audio_confirm_response(response)

        def on_destroy(_dlg: Adw.MessageDialog) -> None:
            if self._audio_confirm_dialog is _dlg:
                self._audio_confirm_dialog = None

        dialog.connect("response", on_response)
        dialog.connect("destroy", on_destroy)
        self._audio_confirm_dialog = dialog
        dialog.present()

    def _on_audio_confirm_response(self, response: str) -> None:
        if self._closed:
            return
        if response == "yes":
            self._stop_audio_line_test()
            return

        if self._audio_line_test_active:
            next_index = self._audio_line_test_index + 1
            if next_index >= len(self._audio_line_test_queue):
                self._stop_audio_line_test()
                self._show_info(
                    "No working source found",
                    "Every available audio input was tried. "
                    "Check HDMI cabling and TV volume, refresh devices, "
                    "or enter a manual PipeWire source name.",
                )
                return
            self._audio_line_test_index = next_index
            source = self._audio_line_test_queue[next_index]
            self._apply_audio_source_for_test(source)
            self._schedule_audio_confirm(warmup_ms=_AUDIO_TEST_WARMUP_MS)
            return

        current = self._current_audio_test_source_fast()
        if current is None:
            return

        def on_queue_ready(queue: list[AudioTestSource]) -> bool:
            if self._closed:
                return False
            next_index = next_queue_index(queue, current.name)
            if next_index is None:
                self._show_info(
                    "No more sources",
                    "There are no further audio inputs to try. "
                    "Enter a manual source name or click Start test to run all sources.",
                )
                return False
            source = queue[next_index]
            self._apply_audio_source_for_test(source)
            self._schedule_audio_confirm(warmup_ms=_AUDIO_TEST_WARMUP_MS)
            return False

        self._build_audio_test_queue_async(on_queue_ready)

    def _refresh_capture_devices(self, *, initial: bool = False) -> None:
        self._capture_refresh_button.set_sensitive(False)
        self._capture_refresh_button.set_label("Scanning…" if not initial else "Loading…")
        self._video_combo.set_sensitive(False)
        self._audio_combo.set_sensitive(False)

        def work() -> None:
            try:
                video = enumerate_v4l2_devices()
                audio = enumerate_audio_sources()
            except Exception:
                video, audio = [], []
            GLib.idle_add(self._apply_capture_devices, video, audio)

        threading.Thread(target=work, daemon=True, name="capture-device-scan").start()

    def _apply_capture_devices(
        self,
        video: list[VideoDeviceOption],
        audio: list[AudioSourceOption],
    ) -> bool:
        if self._closed:
            return False
        self._suppress_live_apply += 1
        try:
            self._video_devices = video
            self._audio_sources = audio
            self._set_combo_model(self._video_combo, self._video_combo_labels())
            self._set_combo_model(self._audio_combo, self._audio_combo_labels())
            self._select_video_from_config()
            self._select_audio_from_config()
            self._video_combo.set_sensitive(True)
            self._audio_combo.set_sensitive(True)
            self._capture_refresh_button.set_sensitive(True)
            self._capture_refresh_button.set_label("Refresh")
        finally:
            self._suppress_live_apply -= 1
        self._update_unsaved_indicator()
        return False

    def _on_refresh_capture_devices(self, *_args) -> None:
        self._refresh_capture_devices(initial=False)

    def _video_device_value(self) -> str:
        selected = self._video_combo.get_selected()
        if selected <= 0:
            return "auto"
        if selected == self._video_manual_index():
            return self._video_manual.get_text().strip()
        return self._video_devices[selected - 1].node

    def _audio_device_value(self) -> str:
        selected = self._audio_combo.get_selected()
        if selected <= 0:
            return "auto"
        if selected == self._audio_manual_index():
            return self._audio_manual.get_text().strip()
        return self._audio_sources[selected - 1].name

    def _resolution_value(self) -> tuple[int, int]:
        index = self._resolution_combo.get_selected()
        if index < 0 or index >= len(RESOLUTION_PRESETS):
            return RESOLUTION_PRESETS[0][1], RESOLUTION_PRESETS[0][2]
        _label, width, height = RESOLUTION_PRESETS[index]
        return width, height

    def _framerate_value(self) -> int:
        index = self._fps_combo.get_selected()
        if index < 0 or index >= len(FRAMERATE_PRESETS):
            return FRAMERATE_PRESETS[0]
        return FRAMERATE_PRESETS[index]

    def _scrcpy_bit_rate_value(self) -> str:
        index = self._scrcpy_bit_rate.get_selected()
        if index < 0 or index >= len(BIT_RATE_PRESETS):
            return ScrcpyConfig.bit_rate
        return BIT_RATE_PRESETS[index]

    def _scrcpy_max_size_value(self) -> int:
        index = self._scrcpy_max_size.get_selected()
        if index < 0 or index >= len(MAX_SIZE_PRESETS):
            return ScrcpyConfig.max_size
        return MAX_SIZE_PRESETS[index][1]

    def _input_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Input and Control")
        self._auto_kbd = self._switch_row(
            "Click video to control",
            "Single click arms remote mode (arrows, scroll, tap). Double-click for keyboard.",
            self._saved_config.input.click_to_control,
        )
        self._release_unfocus = self._switch_row(
            "Release when unfocused",
            "Return to local mode when another app is focused",
            self._saved_config.input.release_on_unfocus,
        )
        self._release_esc = self._switch_row(
            "Esc releases control",
            "Escape stops remote/keyboard capture without sending Back",
            self._saved_config.input.release_on_escape,
        )
        self._scroll = self._spin_row(
            "Scroll threshold", self._saved_config.input.scroll_threshold, 1, 2, 40
        )
        self._soft_unfocused = self._switch_row(
            "Soft buttons when unfocused",
            "Volume and remote buttons work while using other apps",
            self._saved_config.input.soft_buttons_work_unfocused,
        )
        self._default_mode = Adw.ComboRow(title="Default pointer mode")
        modes = Gtk.StringList.new(["D-pad navigation", "Mouse / touch"])
        self._default_mode.set_model(modes)
        self._default_mode.set_selected(
            0 if self._saved_config.input.default_pointer_mode == "nav" else 1
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
            row = self._entry_row(label, f"Default: {default}", self._saved_config.shortcuts.get(action_id))
            self._shortcut_rows[action_id] = row
            group.add(row)
        return group

    def _window_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Window")
        self._remember_geo = self._switch_row(
            "Remember geometry",
            "Restore window size and mode on launch",
            self._saved_config.window.remember_geometry,
        )
        self._aspect = self._switch_row(
            "Lock 16:9 aspect",
            "Keep window aspect ratio when resizing",
            self._saved_config.window.aspect_ratio_locked,
        )
        self._pip_hide = self._switch_row(
            "PiP bar auto-hide",
            "Hide soft buttons in PiP when unfocused",
            self._saved_config.window.pip.soft_bar_auto_hide,
        )
        self._pip_opacity = self._spin_row(
            "PiP opacity", self._saved_config.window.pip.opacity, 0.05, 0.3, 1.0
        )
        self._chrome_hide = self._switch_row(
            "Auto-hide chrome",
            "Hide header and remote bar after idle — even if the mouse is still over the window",
            self._saved_config.window.chrome_auto_hide,
        )
        self._chrome_delay = self._spin_row(
            "Chrome hide delay (ms)",
            self._saved_config.window.chrome_hide_delay_ms,
            250,
            1000,
            15000,
        )
        self._banner_hide = self._spin_row(
            "Control banner hide (ms)",
            self._saved_config.window.banner_auto_hide_ms,
            500,
            0,
            30000,
        )
        self._bar_collapsed = self._switch_row(
            "Start with remote hidden",
            "Collapse the bottom remote bar on launch",
            self._saved_config.window.control_bar_collapsed,
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
            self._saved_config.updates.auto_check_on_launch,
        )
        group.add(self._auto_check_updates)

        self._manifest_override = self._entry_row(
            "Manifest URL override",
            "Leave empty to use the default GitHub releases endpoint",
            self._saved_config.updates.manifest_url_override,
        )
        group.add(self._manifest_override)

        notes_text = self._saved_config.updates.last_release_notes.strip()
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
        if self._closed:
            return False
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
        if self._closed:
            return False
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
            self._saved_config.watch_autostart_enabled,
        )
        self._watch_poll = self._spin_row(
            "Poll interval (s)", self._saved_config.watch_poll_interval_s, 0.5, 1, 30
        )
        group.add(self._watch_enable)
        group.add(self._watch_poll)
        return group

    def _wire_live_apply_handlers(self) -> None:
        switches = [
            self._prefer_wired,
            self._scrcpy_auto,
            self._scrcpy_fullscreen,
            self._scrcpy_no_audio,
            self._scrcpy_stay_awake,
            self._scrcpy_turn_off,
            self._auto_kbd,
            self._release_unfocus,
            self._release_esc,
            self._soft_unfocused,
            self._remember_geo,
            self._aspect,
            self._pip_hide,
            self._chrome_hide,
            self._bar_collapsed,
            self._auto_check_updates,
            self._watch_enable,
        ]
        for widget in switches:
            widget.connect("notify::active", self._on_control_changed)

        combos = [
            self._wired_combo,
            self._wireless_combo,
            self._wireless_port_combo,
            self._scrcpy_bit_rate,
            self._scrcpy_max_size,
            self._resolution_combo,
            self._fps_combo,
            self._video_combo,
            self._audio_combo,
            self._default_mode,
        ]
        for widget in combos:
            widget.connect("notify::selected", self._on_control_changed)

        spins = [
            self._wireless_port_custom,
            self._scroll,
            self._pip_opacity,
            self._chrome_delay,
            self._banner_hide,
            self._watch_poll,
        ]
        for widget in spins:
            widget.connect("notify::value", self._on_control_changed)

        entries = [
            self._wired_manual,
            self._wireless_manual,
            self._video_manual,
            self._audio_manual,
            self._scrcpy_path,
            self._scrcpy_title,
            self._manifest_override,
        ]
        for widget in entries:
            widget.connect("notify::text", self._on_control_changed)
        for row in self._shortcut_rows.values():
            row.connect("notify::text", self._on_control_changed)

    def _on_control_changed(self, *_args) -> None:
        if self._closed or self._suppress_live_apply > 0:
            return
        self._update_unsaved_indicator()
        self._schedule_live_apply()

    def _cancel_live_apply_timer(self) -> None:
        if self._live_apply_timer is not None:
            GLib.source_remove(self._live_apply_timer)
            self._live_apply_timer = None

    def _schedule_live_apply(self) -> None:
        self._cancel_live_apply_timer()
        self._live_apply_timer = GLib.timeout_add(
            _LIVE_APPLY_DEBOUNCE_MS,
            self._do_live_apply,
        )

    def _do_live_apply(self) -> bool:
        self._live_apply_timer = None
        if self._closed:
            return False
        draft = self._assemble_config(strict=False, live=True)
        if draft is not None:
            self._host.schedule_settings_preview(draft)
        return False

    def _update_unsaved_indicator(self) -> None:
        draft = self._assemble_config(strict=False, live=False)
        dirty = draft is not None and configs_differ(draft, self._saved_config)
        self._title_widget.set_subtitle(
            "Unsaved changes" if dirty else "Android TV Connect"
        )

    def _assemble_config(
        self,
        *,
        strict: bool,
        live: bool = False,
    ) -> AppConfig | None:
        shortcuts_kwargs: dict[str, str] = {}
        for action_id, label, _default in SHORTCUT_DEFINITIONS:
            value = self._shortcut_rows[action_id].get_text().strip()
            ok, err = validate_shortcut(value)
            if ok:
                shortcuts_kwargs[action_id] = value
            elif strict:
                self._show_error("Invalid shortcut", f"{label}: {err}")
                return None
            else:
                shortcuts_kwargs[action_id] = self._saved_config.shortcuts.get(action_id)

        shortcuts = replace(self._saved_config.shortcuts, **shortcuts_kwargs)

        wireless_host_raw = self._wireless_host_value()
        port = self._current_wireless_port()
        port, port_err = parse_wireless_port(str(port))
        if port_err or port is None:
            if strict:
                self._show_error(
                    "Invalid wireless port", port_err or "Invalid wireless port"
                )
                return None
            port = self._saved_config.adb.wireless_port

        adb = replace(
            self._saved_config.adb,
            wired_serial=normalize_wired_serial(self._wired_serial_value()),
            wireless_host=normalize_wireless_host(wireless_host_raw),
            wireless_port=port,
        )
        if strict:
            adb, adb_err = validate_adb_for_save(adb)
            if adb_err or adb is None:
                self._show_error("Invalid ADB settings", adb_err or "Invalid ADB settings")
                return None
        else:
            adb = normalize_adb_config(adb)

        width, height = self._resolution_value()
        capture = normalize_capture_config(
            replace(
                self._saved_config.capture,
                video_device=self._video_device_value(),
                audio_device=self._audio_device_value(),
                width=width,
                height=height,
                framerate=self._framerate_value(),
            )
        )
        scrcpy = normalize_scrcpy_config(
            replace(
                self._saved_config.scrcpy,
                auto_launch_on_connect=self._scrcpy_auto.get_active(),
                scrcpy_path=self._scrcpy_path.get_text().strip(),
                max_size=self._scrcpy_max_size_value(),
                bit_rate=self._scrcpy_bit_rate_value(),
                fullscreen=self._scrcpy_fullscreen.get_active(),
                no_audio=self._scrcpy_no_audio.get_active(),
                stay_awake=self._scrcpy_stay_awake.get_active(),
                turn_screen_off=self._scrcpy_turn_off.get_active(),
                window_title=self._scrcpy_title.get_text().strip() or ScrcpyConfig.window_title,
            )
        )
        input_cfg = replace(
            self._saved_config.input,
            prefer_wired_adb=self._prefer_wired.get_active(),
            click_to_control=self._auto_kbd.get_active(),
            release_on_unfocus=self._release_unfocus.get_active(),
            release_on_escape=self._release_esc.get_active(),
            scroll_threshold=self._scroll.get_value(),
            soft_buttons_work_unfocused=self._soft_unfocused.get_active(),
            default_pointer_mode="nav" if self._default_mode.get_selected() == 0 else "mouse",
        )
        window = replace(
            self._saved_config.window,
            remember_geometry=self._remember_geo.get_active(),
            aspect_ratio_locked=self._aspect.get_active(),
            chrome_auto_hide=self._chrome_hide.get_active(),
            chrome_hide_delay_ms=int(self._chrome_delay.get_value()),
            banner_auto_hide_ms=int(self._banner_hide.get_value()),
            control_bar_collapsed=self._bar_collapsed.get_active(),
            pip=replace(
                self._saved_config.window.pip,
                soft_bar_auto_hide=self._pip_hide.get_active(),
                opacity=self._pip_opacity.get_value(),
            ),
        )

        if live:
            updates = self._saved_config.updates
            watch_enabled = self._saved_config.watch_autostart_enabled
            watch_poll = self._saved_config.watch_poll_interval_s
        else:
            updates = replace(
                self._saved_config.updates,
                auto_check_on_launch=self._auto_check_updates.get_active(),
                manifest_url_override=self._manifest_override.get_text().strip(),
                last_release_notes=self._release_notes_row.get_subtitle() or "",
            )
            watch_enabled = self._watch_enable.get_active()
            watch_poll = self._watch_poll.get_value()

        return replace(
            self._saved_config,
            adb=adb,
            capture=capture,
            scrcpy=scrcpy,
            input=input_cfg,
            shortcuts=shortcuts,
            window=window,
            updates=updates,
            watch_autostart_enabled=watch_enabled,
            watch_poll_interval_s=watch_poll,
        )

    def _on_save(self, *_args) -> None:
        if self._closed:
            return
        self._cancel_settings_async_ui()
        updated = self._assemble_config(strict=True, live=False)
        if updated is None:
            return
        callback = self._on_saved
        self._closing_saved = True
        self._closed = True
        self._host.cancel_settings_preview()
        self.close()
        self._host.commit_settings(updated, callback)
