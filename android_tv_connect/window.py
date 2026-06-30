"""Main application window with PiP, control bar, and input forwarding."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import adb_client
from .adb_client import AdbClient
from .capture import CapturePipeline
from .about import show_about_dialog
from .branding import APP_NAME, APP_ID, ICON_NAME
from .chrome import ChromeAutoHide, CompactHeader
from .capture_device import invalidate_capture_cache, is_capture_usb_present
from .config import AppConfig
from .connection_ui import (
    capture_adb_mismatch_warning,
    connection_toast_message,
    detect_hotplug_switch,
    format_adb_chip_label,
    format_adb_chip_tooltip,
    format_capture_chip_label,
    format_capture_chip_tooltip,
)
from .control_bar import ControlBarShell
from .geometry import (
    SavedGeometry,
    clamp_pip_opacity,
    compute_initial_geometry,
    is_tiled_toplevel_state,
    load_window_state,
    min_normal_window_size,
    save_window_state,
    window_height_for_width,
    window_move,
    window_xy,
)
from .pip_window import PipWindow
from .input_controller import (
    InputMode,
    MODE_LABELS,
    forward_key,
    is_soft_release,
    mode_allows_pointer,
    mode_allows_scroll,
    mode_hint,
)
from .shortcuts import bind_shortcuts, humanize_shortcut, key_event_matches
from .scrcpy_manager import (
    SCRCPY_RELAUNCH_COOLDOWN_S,
    ScrcpySession,
    format_scrcpy_exit_message,
    is_scrcpy_available,
    resolve_scrcpy_target,
)
from .settings_apply import SettingsApplyController
from .settings_dialog import SettingsDialog
from .settings_draft import capture_stream_changed, config_snapshot
from .settings_store import load_config, save_config
from .singleton import note_user_quit
from .diagnostics_server import AppDiagnosticsBackend, DiagnosticsServer
from .mpris_controller import (
    CaptureMprisController,
    DEFAULT_STREAM_ARTIST,
    DEFAULT_STREAM_TITLE,
    MprisHandlers,
    PLAYBACK_STOPPED,
)

LOG = logging.getLogger(__name__)

_LIVE_CAPTURE_DEBOUNCE_MS = 300

MODE_NORMAL = "normal"
MODE_PIP = "pip"
MODE_FULLSCREEN = "fullscreen"

CAPTURE_W = 1920
CAPTURE_H = 1080


def _window_toplevel(window: Gtk.Window) -> Gdk.Toplevel | None:
    surface = window.get_surface()
    if isinstance(surface, Gdk.Toplevel):
        return surface
    return None


class VideoSurface(Gtk.Box):
    """Video preview with pointer/keyboard forwarding."""

    def __init__(self, host: "MainWindow") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_focusable(True)
        self.set_can_focus(True)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self._host = host
        self._adb = host.adb
        self._scroll_accum_x = 0.0
        self._scroll_accum_y = 0.0
        self._drag_start: tuple[int, int] | None = None
        self._banner_timer: int | None = None
        self._banner_last_x: float | None = None
        self._banner_last_y: float | None = None

        self._overlay_container = Gtk.Overlay()
        self._overlay_container.set_vexpand(True)
        self._overlay_container.set_hexpand(True)
        self.append(self._overlay_container)

        self._video_host = Gtk.Picture()
        self._video_host.set_vexpand(True)
        self._video_host.set_hexpand(True)
        self._video_host.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._video_host.add_css_class("video-surface")
        self._overlay_container.set_child(self._video_host)

        self._status_overlay = Gtk.Label(label="Waiting for capture…")
        self._status_overlay.add_css_class("dim-label")
        self._status_overlay.set_valign(Gtk.Align.CENTER)
        self._status_overlay.set_halign(Gtk.Align.CENTER)
        self._overlay_container.add_overlay(self._status_overlay)

        self._capture_banner = Gtk.Revealer()
        self._capture_banner.set_reveal_child(False)
        self._capture_banner.set_valign(Gtk.Align.START)
        self._capture_banner.set_halign(Gtk.Align.CENTER)
        self._capture_banner.set_margin_top(8)
        banner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        banner_box.add_css_class("caption")
        banner_box.add_css_class("control-banner")
        self._capture_banner_label = Gtk.Label()
        banner_box.append(self._capture_banner_label)
        release_btn = Gtk.Button(label="Release")
        release_btn.connect("clicked", lambda *_: self._host.set_input_mode(InputMode.LOCAL, source="release"))
        banner_box.append(release_btn)
        self._capture_banner.set_child(banner_box)
        self._overlay_container.add_overlay(self._capture_banner)

        self._hint_revealer = Gtk.Revealer()
        self._hint_revealer.set_reveal_child(False)
        self._hint_revealer.set_valign(Gtk.Align.END)
        self._hint_revealer.set_halign(Gtk.Align.CENTER)
        self._hint_revealer.set_margin_bottom(52)
        hint = Gtk.Label(label=mode_hint(InputMode.LOCAL, ""))
        hint.add_css_class("caption")
        hint.add_css_class("hint-banner")
        self._hint_label = hint
        self._hint_revealer.set_child(hint)
        self._overlay_container.add_overlay(self._hint_revealer)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key)

    @property
    def video_host(self) -> Gtk.Picture:
        return self._video_host

    @property
    def config(self) -> AppConfig:
        return self._host.config

    def set_status(self, text: str | None) -> None:
        if text:
            self._status_overlay.set_text(text)
            self._status_overlay.set_visible(True)
        else:
            self._status_overlay.set_visible(False)

    def update_input_mode(self, mode: InputMode) -> None:
        release = humanize_shortcut(self._host.config.shortcuts.release_control)
        self._video_host.remove_css_class("remote-armed")
        self._video_host.remove_css_class("control-active")

        window_active = bool(self.get_root() and self.get_root().is_active())
        show_idle_hint = mode == InputMode.LOCAL and window_active
        self._hint_revealer.set_reveal_child(show_idle_hint)
        if show_idle_hint:
            self._hint_label.set_text(mode_hint(InputMode.LOCAL, release))

        if mode == InputMode.REMOTE:
            self._video_host.add_css_class("remote-armed")
            self._capture_banner_label.set_text(mode_hint(InputMode.REMOTE, release))
            self._capture_banner.set_reveal_child(True)
            self._schedule_banner_hide()
        elif mode == InputMode.KEYBOARD:
            self._video_host.add_css_class("control-active")
            self._capture_banner_label.set_text(mode_hint(InputMode.KEYBOARD, release))
            self._capture_banner.set_reveal_child(True)
            self._schedule_banner_hide()
        else:
            self._cancel_banner_hide()
            self._capture_banner.set_reveal_child(False)

    def _schedule_banner_hide(self) -> None:
        self._cancel_banner_hide()
        delay = self._host.config.window.banner_auto_hide_ms
        if delay <= 0:
            return
        self._banner_timer = GLib.timeout_add(delay, self._hide_banner)

    def _cancel_banner_hide(self) -> None:
        if self._banner_timer is not None:
            GLib.source_remove(self._banner_timer)
            self._banner_timer = None

    def _hide_banner(self) -> bool:
        self._banner_timer = None
        if self._host.input_mode in (InputMode.REMOTE, InputMode.KEYBOARD):
            self._capture_banner.set_reveal_child(False)
        return False

    def _on_motion(self, _ctrl, x: float, y: float) -> None:
        self._host.record_chrome_pointer(x, y)
        if self._host.input_mode not in (InputMode.REMOTE, InputMode.KEYBOARD):
            return
        if not self._pointer_moved(self._banner_last_x, self._banner_last_y, x, y):
            return
        self._banner_last_x = x
        self._banner_last_y = y
        if self._capture_banner.get_reveal_child() is False:
            self._capture_banner.set_reveal_child(True)
        self._schedule_banner_hide()

    @staticmethod
    def _pointer_moved(
        last_x: float | None, last_y: float | None, x: float, y: float, threshold: float = 3.0
    ) -> bool:
        if last_x is None or last_y is None:
            return True
        return abs(x - last_x) >= threshold or abs(y - last_y) >= threshold

    def set_mouse_mode(self, active: bool) -> None:
        if active:
            self._video_host.add_css_class("mouse-mode")
        else:
            self._video_host.remove_css_class("mouse-mode")

    def _window_active(self) -> bool:
        root = self.get_root()
        return root is not None and root.is_active()

    def _map_coords(self, widget_x: float, widget_y: float) -> tuple[int, int] | None:
        alloc = self._video_host.get_allocation()
        if alloc.width <= 0 or alloc.height <= 0:
            return None

        src_aspect = CAPTURE_W / CAPTURE_H
        dst_aspect = alloc.width / alloc.height
        if dst_aspect > src_aspect:
            video_h = alloc.height
            video_w = video_h * src_aspect
            off_x = (alloc.width - video_w) / 2
            off_y = 0.0
        else:
            video_w = alloc.width
            video_h = video_w / src_aspect
            off_x = 0.0
            off_y = (alloc.height - video_h) / 2

        lx = widget_x - off_x
        ly = widget_y - off_y
        if lx < 0 or ly < 0 or lx > video_w or ly > video_h:
            return None

        ax = int(lx / video_w * CAPTURE_W)
        ay = int(ly / video_h * CAPTURE_H)
        return max(0, min(CAPTURE_W, ax)), max(0, min(CAPTURE_H, ay))

    def _control_allowed(self) -> bool:
        if not self._adb.is_connected():
            return False
        if self.config.input.keyboard_requires_focus and not self._window_active():
            return False
        return True

    def _pointer_allowed(self) -> bool:
        return self._control_allowed() and mode_allows_pointer(self._host.input_mode)

    def _on_click(self, _gesture, n_press: int, x: float, y: float) -> None:
        if not self._control_allowed():
            return

        self.grab_focus()
        self._host.bump_chrome()

        if (
            self._host.input_mode == InputMode.LOCAL
            and self.config.input.click_to_control
        ):
            self._host.set_input_mode(InputMode.REMOTE, source="click")

        if not mode_allows_pointer(self._host.input_mode):
            return

        mapped = self._map_coords(x, y)
        if mapped:
            self._adb.tap(*mapped)

        # Double-click video promotes to full keyboard capture.
        if n_press == 2:
            self._host.set_input_mode(InputMode.KEYBOARD, source="double-click")

    def _on_drag_begin(self, gesture, start_x: float, start_y: float) -> None:
        if not self._host.mouse_mode or not self._pointer_allowed():
            self._drag_start = None
            return
        mapped = self._map_coords(start_x, start_y)
        self._drag_start = mapped

    def _on_drag_update(self, _gesture, offset_x: float, offset_y: float) -> None:
        _ = (offset_x, offset_y)

    def _on_drag_end(self, gesture, offset_x: float, offset_y: float) -> None:
        if self._drag_start is None or not self._host.mouse_mode:
            return
        start_x, start_y = gesture.get_start_point()
        end = self._map_coords(start_x + offset_x, start_y + offset_y)
        if end and (abs(offset_x) > 4 or abs(offset_y) > 4):
            self._adb.swipe(self._drag_start[0], self._drag_start[1], end[0], end[1], 200)
        self._drag_start = None

    def _on_scroll(self, _ctrl, dx: float, dy: float) -> bool:
        if not self._control_allowed() or self._host.mouse_mode:
            return False
        if not mode_allows_scroll(self._host.input_mode):
            return False

        threshold = self.config.input.scroll_threshold
        self._scroll_accum_x += dx
        self._scroll_accum_y += dy

        if abs(self._scroll_accum_x) >= threshold:
            code = (
                adb_client.KEYCODE_DPAD_LEFT
                if self._scroll_accum_x < 0
                else adb_client.KEYCODE_DPAD_RIGHT
            )
            self._adb.keyevent(code)
            self._scroll_accum_x = 0.0
            self._host.bump_chrome()

        if abs(self._scroll_accum_y) >= threshold:
            code = (
                adb_client.KEYCODE_DPAD_UP
                if self._scroll_accum_y < 0
                else adb_client.KEYCODE_DPAD_DOWN
            )
            self._adb.keyevent(code)
            self._scroll_accum_y = 0.0
            self._host.bump_chrome()
        return True

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        release_spec = self.config.shortcuts.release_control
        if key_event_matches(keyval, state, release_spec):
            self._host.set_input_mode(InputMode.LOCAL, source="shortcut")
            return True

        mode = self._host.input_mode
        if (
            self.config.input.release_on_escape
            and is_soft_release(keyval, state)
            and mode != InputMode.LOCAL
        ):
            self._host.set_input_mode(InputMode.LOCAL, source="escape")
            return True

        if not self._adb.is_connected() or not self._control_allowed():
            return False

        if mode == InputMode.LOCAL:
            return False

        return forward_key(self._adb, keyval, state, mode)


class MainWindow(Adw.ApplicationWindow):
    """Primary viewer window."""

    def __init__(self, app: Adw.Application, config: AppConfig) -> None:
        super().__init__(application=app)
        self._config = config
        self._state = load_window_state()
        self._mode = self._state["last_mode"]
        self._save_timer: int | None = None
        self._aspect_adjusting = False
        self._wm_geometry_managed = False
        self._saved_normal: SavedGeometry | None = None
        self._pip_window: PipWindow | None = None
        self._mouse_mode = config.input.default_pointer_mode == "mouse"
        self._input_mode = InputMode.LOCAL
        self._settings_dialog: SettingsDialog | None = None
        self._settings_apply = SettingsApplyController(self)
        self._live_capture_timer: int | None = None
        self._live_capture_pending = None
        self._scrcpy = ScrcpySession(
            on_state_change=self._on_scrcpy_state_changed,
            on_quick_exit=self._on_scrcpy_quick_exit,
        )
        self._scrcpy_launch_pending = False
        self._scrcpy_cooldown_until = 0.0
        self._scrcpy_user_stopped = False
        self._known_usb_serials: set[str] = set()
        self._hotplug_dismissed: set[str] = set()
        self._mismatch_dismissed = False

        self.set_title(APP_NAME)
        self.set_icon_name(ICON_NAME)

        self._adb = AdbClient(
            wired_serial=self._config.adb.wired_serial,
            wireless_host=self._config.adb.wireless_host,
            wireless_port=self._config.adb.wireless_port,
            prefer_wired=self._config.input.prefer_wired_adb,
            on_connection_change=self._on_adb_connection_changed,
        )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._mismatch_banner = Gtk.Revealer()
        self._mismatch_banner.set_reveal_child(False)
        mismatch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mismatch_box.add_css_class("caption")
        mismatch_box.add_css_class("control-banner")
        mismatch_box.set_margin_top(4)
        mismatch_box.set_margin_bottom(4)
        mismatch_box.set_margin_start(8)
        mismatch_box.set_margin_end(8)
        mismatch_box.set_halign(Gtk.Align.FILL)
        self._mismatch_banner_label = Gtk.Label(xalign=0)
        self._mismatch_banner_label.set_wrap(True)
        self._mismatch_banner_label.set_hexpand(True)
        mismatch_box.append(self._mismatch_banner_label)
        mismatch_dismiss = Gtk.Button(label="Dismiss")
        mismatch_dismiss.connect("clicked", lambda *_: self.dismiss_mismatch_banner())
        mismatch_box.append(mismatch_dismiss)
        self._mismatch_banner.set_child(mismatch_box)
        root.append(self._mismatch_banner)

        self._toolbar = Adw.ToolbarView()
        self._headerbar = Adw.HeaderBar()
        self._compact_header = CompactHeader(self._headerbar, self)
        self._toolbar.add_top_bar(self._headerbar)

        self._content_overlay = Gtk.Overlay()
        self._content_overlay.set_vexpand(True)
        self._content_overlay.set_hexpand(True)

        self._video = VideoSurface(self)
        self._content_overlay.set_child(self._video)

        self._control_shell = ControlBarShell(self._adb, self)
        self._control_shell.set_valign(Gtk.Align.END)
        self._control_shell.set_halign(Gtk.Align.CENTER)
        self._content_overlay.add_overlay(self._control_shell)

        root.append(self._content_overlay)

        self._toolbar.set_content(root)
        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(self._toolbar)
        self.set_content(self._toast_overlay)

        self._chrome = ChromeAutoHide(
            self._control_shell,
            delay_ms=self._config.window.chrome_hide_delay_ms,
            enabled=self._config.window.chrome_auto_hide,
        )

        self._install_chrome_motion(self)
        self._install_chrome_motion(self._headerbar)

        self._capture_usb_dialog: Adw.MessageDialog | None = None

        self._capture = CapturePipeline(
            config=self._config.capture,
            on_state_change=self._on_capture_state,
            on_error=lambda msg: LOG.error(msg),
            on_usb_unplugged=self._on_capture_usb_unplugged,
            on_usb_reconnected=self._on_capture_usb_reconnected,
        )
        self._diagnostics_backend = AppDiagnosticsBackend(self)

        self.connect("close-request", self._on_close_request)
        self.connect("realize", self._on_realize)
        self.connect("notify::maximized", self._on_wm_geometry_changed)
        self.connect("notify::width", self._on_size_changed)
        self.connect("notify::height", self._on_size_changed)
        self.connect("notify::is-active", self._on_focus_changed)

        self._shortcut_ctrl: Gtk.ShortcutController | None = None
        self._install_shortcuts()
        self._apply_mode(self._mode, initial=True)
        self._adb.connect()
        self._known_usb_serials = set(self._adb.list_usb_serials())
        self._update_status_dots()
        GLib.timeout_add_seconds(2, self._status_tick)

        target = (
            self._pip_window.picture
            if self._pip_window is not None
            else self._video.video_host
        )
        self._capture.attach_video_widget(target)
        if not self._capture.start():
            self._video.set_status("Capture device not ready")

        self._register_mpris_handlers()

        self._control_shell.set_mouse_mode(self._mouse_mode)
        self.set_input_mode(InputMode.LOCAL, source="init")

    @property
    def input_mode(self) -> InputMode:
        return self._input_mode

    @property
    def keyboard_capture(self) -> bool:
        return self._input_mode == InputMode.KEYBOARD

    def set_mouse_mode(self, active: bool) -> None:
        self._mouse_mode = active
        self._video.set_mouse_mode(active)
        self._control_shell.set_mouse_mode(active)

    @property
    def adb(self) -> AdbClient:
        return self._adb

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def mouse_mode(self) -> bool:
        return self._mouse_mode

    def set_input_mode(self, mode: InputMode, *, source: str = "manual") -> None:
        if source == "init" and mode != InputMode.LOCAL:
            mode = InputMode.LOCAL

        if (
            source == "unfocus"
            and not self._config.input.release_on_unfocus
        ):
            return

        if mode == InputMode.KEYBOARD and not self._control_connected():
            mode = InputMode.LOCAL

        self._input_mode = mode
        self._compact_header.set_mode_label(MODE_LABELS[mode])
        self._video.update_input_mode(mode)
        self._control_shell.set_input_mode(mode)
        self._update_status_dots()

    def _control_connected(self) -> bool:
        return self._adb.is_connected()

    def set_keyboard_capture(self, active: bool, *, source: str = "manual") -> None:
        """Compatibility wrapper for older call sites."""
        self.set_input_mode(
            InputMode.KEYBOARD if active else InputMode.LOCAL,
            source=source,
        )

    def dismiss_mismatch_banner(self) -> None:
        self._mismatch_dismissed = True
        self._mismatch_banner.set_reveal_child(False)

    def refresh_and_connect(self) -> None:
        threading.Thread(
            target=self._refresh_connect_worker,
            daemon=True,
            name="refresh-connect",
        ).start()

    def _refresh_connect_worker(self) -> None:
        invalidate_capture_cache()
        connected = self._adb.refresh_connection()
        capture_ok = True
        if self._capture.is_running():
            self._capture.stop()
            capture_ok = self._capture.start()
        GLib.idle_add(self._on_refresh_connect_done, connected, capture_ok)

    def _on_refresh_connect_done(self, connected: bool, capture_ok: bool) -> bool:
        self._known_usb_serials = set(self._adb.list_usb_serials())
        self._mismatch_dismissed = False
        self._update_status_dots()
        if connected:
            serial = self._adb.active_serial()
            if serial:
                self._show_toast(
                    connection_toast_message(
                        serial,
                        is_wireless=self._adb.is_wireless_active(),
                    )
                )
            if self._capture.state == "playing" and self._input_mode == InputMode.LOCAL:
                self._video.set_status(None)
        else:
            self._video.set_status("ADB connection failed — use Refresh & connect")
        if not capture_ok and self._capture.is_running() is False:
            self._video.set_status("Capture device not ready")
        return False

    def _show_toast(
        self,
        message: str,
        *,
        button_label: str | None = None,
        on_button=None,
        timeout: int = 5,
    ) -> Adw.Toast:
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        if button_label:
            toast.set_button_label(button_label)
            if on_button is not None:
                toast.connect("button-clicked", on_button)
        self._toast_overlay.add_toast(toast)
        return toast

    def _hotplug_watch_serial(self) -> str | None:
        configured = self._config.adb.wired_serial.strip()
        if configured:
            return configured
        active = self._adb.active_serial()
        if active and ":" not in active:
            return active
        return None

    def _check_hotplug(self) -> None:
        current = set(self._adb.list_usb_serials())
        new_serial = detect_hotplug_switch(
            previous_usb=self._known_usb_serials,
            current_usb=current,
            watch_serial=self._hotplug_watch_serial(),
            dismissed=self._hotplug_dismissed,
        )
        self._known_usb_serials = current
        if new_serial:
            self._offer_hotplug_switch(new_serial)

    def _offer_hotplug_switch(self, serial: str) -> None:
        toast = Adw.Toast.new(f"New device detected: {serial} — Switch?")
        toast.set_button_label("Switch")
        toast.set_timeout(12)

        def on_switch(_toast: Adw.Toast) -> None:
            self._hotplug_dismissed.add(serial)
            config = replace(
                self._config,
                adb=replace(self._config.adb, wired_serial=serial),
            )
            self._config = config
            save_config(config)
            self._adb.update_settings(
                wired_serial=serial,
                wireless_host=config.adb.wireless_host,
                wireless_port=config.adb.wireless_port,
                prefer_wired=config.input.prefer_wired_adb,
            )
            self._show_toast(f"Switched ADB to {serial}")

        def on_dismissed(_toast: Adw.Toast) -> None:
            self._hotplug_dismissed.add(serial)

        toast.connect("button-clicked", on_switch)
        toast.connect("dismissed", on_dismissed)
        self._toast_overlay.add_toast(toast)

    def open_settings(self) -> None:
        self.set_input_mode(InputMode.LOCAL, source="settings")
        try:
            if self._settings_dialog is not None:
                self._settings_dialog.present()
                return
            self._settings_dialog = SettingsDialog(
                self, self._config, self, self._on_settings_saved
            )
            self._settings_dialog.connect("destroy", self._on_settings_closed)
            self._settings_dialog.present()
        except Exception:
            LOG.exception("Failed to open settings")

    def open_about(self) -> None:
        try:
            show_about_dialog(self)
        except Exception:
            LOG.exception("Failed to open about dialog")

    def _on_settings_closed(self, *_args) -> None:
        self._settings_apply.cancel_pending()
        self._settings_dialog = None

    def _install_chrome_motion(self, widget: Gtk.Widget) -> None:
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_chrome_motion)
        widget.add_controller(motion)

    def _on_chrome_motion(self, _ctrl, x: float, y: float) -> None:
        self.record_chrome_pointer(x, y)

    def record_chrome_pointer(self, x: float, y: float) -> None:
        if self._mode in (MODE_NORMAL, MODE_FULLSCREEN):
            self._chrome.record_pointer(x, y)

    def toggle_control_bar(self) -> None:
        self._control_shell.toggle_user()

    def persist_control_bar_collapsed(self, collapsed: bool) -> None:
        if self._config.window.control_bar_collapsed == collapsed:
            return
        self._config = replace(self._config, window=replace(
            self._config.window,
            control_bar_collapsed=collapsed,
        ))
        save_config(self._config)

    def bump_chrome(self) -> None:
        if self._mode in (MODE_NORMAL, MODE_FULLSCREEN):
            self._chrome.bump()

    def cancel_settings_preview(self) -> None:
        """Cancel debounced preview and pending capture restarts."""
        self._settings_apply.cancel_pending()

    def schedule_settings_preview(self, config: AppConfig) -> None:
        """Debounced live preview while the settings dialog is open."""
        self._settings_apply.schedule_live_preview(config)

    def schedule_settings_revert(self, saved: AppConfig) -> None:
        """Restore saved settings after cancel/close without blocking the dialog."""
        self._settings_apply.schedule_revert(saved)

    def commit_settings(
        self,
        config: AppConfig,
        on_complete=None,
    ) -> None:
        """Persist settings on a worker thread, then apply on the GTK loop."""
        self._settings_apply.commit_saved(config, on_complete)

    def cancel_live_capture_apply(self) -> None:
        if self._live_capture_timer is not None:
            GLib.source_remove(self._live_capture_timer)
            self._live_capture_timer = None
        self._live_capture_pending = None

    def apply_settings_live(self, config: AppConfig) -> None:
        """Apply settings without persisting (legacy entry for callers)."""
        self.apply_settings_core(config)

    def apply_settings_core(self, config: AppConfig) -> None:
        """Apply settings to the running app without writing config.json."""
        prev_config = config_snapshot(self._config)
        prev_capture = prev_config.capture
        self._config = config

        self._chrome.set_enabled(config.window.chrome_auto_hide)
        self._chrome.set_delay_ms(config.window.chrome_hide_delay_ms)
        if config.window.control_bar_collapsed:
            self._control_shell.collapse_user()
        else:
            self._control_shell.expand_user()

        self._mouse_mode = config.input.default_pointer_mode == "mouse"
        self._control_shell.set_mouse_mode(self._mouse_mode)

        if self._mode == MODE_PIP and self._pip_window is not None:
            self._state["pip_opacity"] = config.window.pip.opacity
            self._pip_window.update_opacity(config.window.pip.opacity)
            self._pip_window.update_config(config.window.pip)

        if prev_config.shortcuts != config.shortcuts:
            GLib.idle_add(self._install_shortcuts_idle)
        else:
            self._update_shortcut_tooltips()
        self._video.update_input_mode(self._input_mode)

        if self._capture.is_running():
            if capture_stream_changed(prev_capture, config.capture):
                self._schedule_live_capture_apply(config.capture)
            else:
                self._capture.config = config.capture
        else:
            self._capture.config = config.capture

        adb_changed = (
            prev_config.adb != config.adb
            or prev_config.input.prefer_wired_adb != config.input.prefer_wired_adb
        )
        if adb_changed:
            adb_cfg = config_snapshot(config)

            def apply_adb() -> None:
                try:
                    self._adb.update_settings(
                        wired_serial=adb_cfg.adb.wired_serial,
                        wireless_host=adb_cfg.adb.wireless_host,
                        wireless_port=adb_cfg.adb.wireless_port,
                        prefer_wired=adb_cfg.input.prefer_wired_adb,
                    )
                except Exception:
                    LOG.exception("Failed to apply ADB settings")

            threading.Thread(
                target=apply_adb,
                daemon=True,
                name="adb-settings-apply",
            ).start()

    def _install_shortcuts_idle(self) -> bool:
        self._install_shortcuts()
        return False

    def _schedule_live_capture_apply(self, capture_cfg) -> None:
        self._live_capture_pending = capture_cfg
        if self._live_capture_timer is not None:
            return
        self._live_capture_timer = GLib.timeout_add(
            _LIVE_CAPTURE_DEBOUNCE_MS,
            self._flush_live_capture_apply,
        )

    def _flush_live_capture_apply(self) -> bool:
        self._live_capture_timer = None
        pending = self._live_capture_pending
        self._live_capture_pending = None
        if pending is not None and self._capture.is_running():
            threading.Thread(
                target=self._capture.with_config,
                args=(pending,),
                daemon=True,
                name="capture-live-apply",
            ).start()
        return False

    def _on_settings_saved(self, config: AppConfig) -> None:
        """Settings were persisted and applied by commit_settings."""
        _ = config

    def toggle_keyboard_capture(self) -> None:
        if self._input_mode == InputMode.KEYBOARD:
            self.set_input_mode(InputMode.REMOTE, source="shortcut")
        else:
            self.set_input_mode(InputMode.KEYBOARD, source="shortcut")

    def _update_shortcut_tooltips(self) -> None:
        sc = self._config.shortcuts
        self._compact_header.update_tooltips(
            humanize_shortcut(sc.pip_toggle),
            humanize_shortcut(sc.fullscreen_toggle),
        )

    def _install_shortcuts(self) -> None:
        if self._shortcut_ctrl is not None:
            self.remove_controller(self._shortcut_ctrl)
            self._shortcut_ctrl = None

        actions = {
            "pip_toggle": self.toggle_pip,
            "fullscreen_toggle": self.toggle_fullscreen,
            "release_control": lambda: self.set_input_mode(
                InputMode.LOCAL, source="shortcut"
            ),
            "keyboard_capture_toggle": self.toggle_keyboard_capture,
            "mouse_mode_toggle": lambda: self.set_mouse_mode(not self._mouse_mode),
            "open_settings": self.open_settings,
            "control_bar_toggle": self.toggle_control_bar,
            "mirror_toggle": self.toggle_mirror,
        }
        self._shortcut_ctrl = bind_shortcuts(self, self._config.shortcuts, actions)
        self._update_shortcut_tooltips()

    def on_mirror_chip_clicked(self) -> None:
        if self._scrcpy.is_running():
            self._scrcpy_user_stopped = True
            self.stop_mirror()
            return
        if self._scrcpy_launch_pending:
            return
        if time.monotonic() < self._scrcpy_cooldown_until:
            return
        self._scrcpy_user_stopped = False
        self._scrcpy_launch_pending = True
        threading.Thread(target=self._mirror_launch_worker, daemon=True).start()

    def toggle_mirror(self) -> None:
        self.on_mirror_chip_clicked()

    def stop_mirror(self) -> None:
        self._scrcpy.stop()
        self._scrcpy_launch_pending = False
        self._update_status_dots()

    def _mirror_launch_worker(self) -> None:
        argv, error = resolve_scrcpy_target(self._config, self._adb)
        if argv is None:
            GLib.idle_add(self._on_mirror_launch_failed, error or "Unknown error")
            return
        ok, launch_error = self._scrcpy.launch(argv)
        if not ok:
            GLib.idle_add(self._on_mirror_launch_failed, launch_error or "Launch failed")
            return
        GLib.idle_add(self._on_mirror_launch_done)

    def _on_mirror_launch_failed(self, message: str) -> bool:
        self._scrcpy_launch_pending = False
        self._update_status_dots()
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Screen mirror unavailable",
            body=message,
        )
        dialog.add_response("ok", "OK")
        dialog.present()
        return False

    def _on_mirror_launch_done(self) -> bool:
        self._scrcpy_launch_pending = False
        self._update_status_dots()
        return False

    def _on_scrcpy_quick_exit(self, exit_code: int, log_lines: list[str]) -> None:
        GLib.idle_add(self._on_scrcpy_quick_exit_idle, exit_code, log_lines)

    def _on_scrcpy_quick_exit_idle(
        self,
        exit_code: int,
        log_lines: list[str],
    ) -> bool:
        self._scrcpy_launch_pending = False
        self._scrcpy_cooldown_until = time.monotonic() + SCRCPY_RELAUNCH_COOLDOWN_S
        self._update_status_dots()
        message = format_scrcpy_exit_message(exit_code, log_lines)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Screen mirror stopped",
            body=message,
        )
        dialog.add_response("ok", "OK")
        dialog.present()
        return False

    def _on_scrcpy_state_changed(self, running: bool) -> None:
        GLib.idle_add(self._on_scrcpy_state_idle, running)

    def _on_scrcpy_state_idle(self, running: bool) -> bool:
        if not running:
            self._scrcpy_launch_pending = False
        self._update_status_dots()
        return False

    def _maybe_auto_launch_scrcpy(self) -> None:
        if not self._config.scrcpy.auto_launch_on_connect:
            return
        if self._scrcpy_user_stopped:
            return
        if self._scrcpy.is_running() or self._scrcpy_launch_pending:
            return
        if time.monotonic() < self._scrcpy_cooldown_until:
            return
        if not is_scrcpy_available(self._config.scrcpy.scrcpy_path):
            return
        if not self._adb.is_connected():
            return
        self._scrcpy_launch_pending = True
        threading.Thread(target=self._mirror_launch_worker, daemon=True).start()

    def on_adb_chip_clicked(self) -> None:
        self.refresh_and_connect()

    def on_capture_chip_clicked(self) -> None:
        if self._capture.state == "playing":
            self._capture.pause_by_user()
            self._video.set_status("Capture paused — click Capture to resume")
        else:
            self._capture.resume_by_user()
            if self._capture.state != "playing":
                self._video.set_status("Reconnecting capture…")
        self._update_status_dots()

    def _update_status_dots(self) -> None:
        capture_playing = self._capture.state == "playing"
        adb_connected = self._adb.is_connected()
        mirror_running = self._scrcpy.is_running()
        adb_serial = self._adb.active_serial()
        adb_wireless = self._adb.is_wireless_active()
        capture_device = self._capture.effective_video_device

        self._compact_header.set_dot(self._compact_header.capture_chip, capture_playing)
        self._compact_header.set_dot(self._compact_header.adb_chip, adb_connected)
        self._compact_header.set_dot(self._compact_header.mirror_chip, mirror_running)

        self._compact_header.set_chip_label(
            self._compact_header.capture_chip,
            format_capture_chip_label(device=capture_device, playing=capture_playing),
        )
        self._compact_header.set_chip_tooltip(
            self._compact_header.capture_chip,
            format_capture_chip_tooltip(
                device=capture_device,
                playing=capture_playing,
                user_paused=self._capture.user_paused,
                state=self._capture.state,
            ),
        )

        self._compact_header.set_chip_label(
            self._compact_header.adb_chip,
            format_adb_chip_label(
                connected=adb_connected,
                serial=adb_serial,
                is_wireless=adb_wireless,
            ),
        )
        self._compact_header.set_chip_tooltip(
            self._compact_header.adb_chip,
            format_adb_chip_tooltip(
                connected=adb_connected,
                serial=adb_serial,
                is_wireless=adb_wireless,
                action_hint="click to refresh & connect",
            ),
        )

        if mirror_running:
            transport = self._scrcpy.active_transport_label(self._adb)
            mirror_tip = f"scrcpy running ({transport}) — click to stop"
        elif is_scrcpy_available(self._config.scrcpy.scrcpy_path):
            if adb_connected:
                transport = self._scrcpy.active_transport_label(self._adb)
                mirror_tip = f"Mirror screen via scrcpy ({transport} ADB) — click to start"
            else:
                mirror_tip = "Mirror (scrcpy) — connect ADB first"
        else:
            mirror_tip = "scrcpy not installed — sudo apt install scrcpy"
        self._compact_header.set_chip_tooltip(
            self._compact_header.mirror_chip,
            mirror_tip,
        )

        usb_serials = self._adb.list_usb_serials()
        wireless_count = len(self._adb.list_wireless_devices())
        wired_auto = not self._config.adb.wired_serial.strip()
        mismatch = None
        if not self._mismatch_dismissed and not wired_auto:
            mismatch = capture_adb_mismatch_warning(
                capture_usb_present=is_capture_usb_present(self._config.capture),
                adb_connected=adb_connected,
                adb_serial=adb_serial,
                adb_is_wireless=adb_wireless,
                usb_serials=usb_serials,
                wireless_count=wireless_count,
            )
        if mismatch:
            self._mismatch_banner_label.set_text(mismatch)
            self._mismatch_banner.set_reveal_child(True)
        else:
            self._mismatch_banner.set_reveal_child(False)

    def _on_adb_connection_changed(self, connected: bool) -> None:
        GLib.idle_add(self._refresh_status_dots)
        if connected:
            GLib.idle_add(self._maybe_auto_launch_scrcpy_idle)

    def _maybe_auto_launch_scrcpy_idle(self) -> bool:
        self._maybe_auto_launch_scrcpy()
        return False

    def _refresh_status_dots(self) -> bool:
        self._update_status_dots()
        return False

    def _status_tick(self) -> bool:
        self._check_hotplug()
        self._update_status_dots()
        return True

    def _on_capture_state(self, state: str) -> None:
        if state == "playing":
            self._video.set_status(None)
        elif state == "paused":
            self._video.set_status("Capture paused — click Capture to resume")
        elif state == "disconnected":
            self._video.set_status("Capture USB disconnected — reconnect the dongle")
        elif state == "waiting":
            self._video.set_status("Waiting for capture device…")
        elif state in ("reconnecting", "starting"):
            device = self._capture.effective_video_device
            hint = f" ({device})" if device else ""
            self._video.set_status(f"Capture reconnecting{hint}…")
        if self._pip_window is not None:
            self._pip_window.set_status(self._video_status_text())
        self._update_status_dots()
        self._sync_mpris_from_capture(state)

    def _register_mpris_handlers(self) -> None:
        app = self.get_application()
        if not isinstance(app, AndroidTvApp):
            return
        app.mpris.set_handlers(
            MprisHandlers(
                on_play=self._mpris_play,
                on_pause=self._mpris_pause,
                on_raise=self._mpris_raise,
                get_volume=self._capture.get_audio_volume,
                set_volume=self._capture.set_audio_volume,
            )
        )
        app.mpris.set_metadata(DEFAULT_STREAM_TITLE, DEFAULT_STREAM_ARTIST)
        self._sync_mpris_from_capture(self._capture.state)

    def _clear_mpris_handlers(self) -> None:
        app = self.get_application()
        if not isinstance(app, AndroidTvApp):
            return
        app.mpris.clear_handlers()
        app.mpris.set_playback_status(PLAYBACK_STOPPED)

    def _sync_mpris_from_capture(self, state: str) -> None:
        app = self.get_application()
        if not isinstance(app, AndroidTvApp):
            return
        app.mpris.sync_from_capture(state, user_paused=self._capture.user_paused)
        volume = self._capture.get_audio_volume()
        if volume is not None:
            app.mpris.set_volume(volume)

    def _mpris_play(self) -> None:
        if self._capture.state != "playing":
            self._capture.resume_by_user()
            if self._capture.state != "playing":
                self._video.set_status("Reconnecting capture…")
            self._update_status_dots()

    def _mpris_pause(self) -> None:
        if self._capture.state == "playing":
            self._capture.pause_by_user()
            self._video.set_status("Capture paused — click Capture to resume")
            self._update_status_dots()

    def _mpris_raise(self) -> None:
        if self._pip_window is not None:
            self._pip_window.present()
        else:
            self.present()

    def _on_capture_usb_unplugged(self) -> None:
        GLib.idle_add(self._show_capture_usb_dialog)

    def _on_capture_usb_reconnected(self) -> None:
        GLib.idle_add(self._dismiss_capture_usb_dialog)

    def _show_capture_usb_dialog(self) -> bool:
        if self._capture_usb_dialog is not None:
            return False
        dialog = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading="Capture dongle disconnected",
            body=(
                "The HDMI capture USB cable or dongle was unplugged.\n\n"
                "Reconnect the MacroSilicon USB device to resume video. "
                "The app will reconnect automatically."
            ),
        )
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.connect("response", self._dismiss_capture_usb_dialog_response)
        dialog.connect("destroy", self._on_capture_usb_dialog_closed)
        self._capture_usb_dialog = dialog
        dialog.present()
        self._update_status_dots()
        return False

    def _dismiss_capture_usb_dialog_response(self, dialog, _response: str) -> None:
        dialog.close()

    def _on_capture_usb_dialog_closed(self, *_args) -> None:
        self._capture_usb_dialog = None

    def _dismiss_capture_usb_dialog(self) -> bool:
        if self._capture_usb_dialog is not None:
            self._capture_usb_dialog.close()
        self._update_status_dots()
        return False

    def _on_close_request(self, *_args) -> bool:
        if self._pip_window is not None:
            self._exit_pip(restore_main=False)
        self._persist_geometry()
        self._clear_mpris_handlers()
        self._diagnostics_backend.shutdown()
        self._scrcpy.stop()
        self._capture.stop()
        self._adb.disconnect()
        app = self.get_application()
        if isinstance(app, AndroidTvApp):
            app._on_window_destroy()
        return False

    def _on_realize(self, *_args) -> None:
        toplevel = _window_toplevel(self)
        if toplevel is not None:
            toplevel.connect("state-changed", self._on_toplevel_state_changed)
        self._sync_normal_size_constraints()

    def _on_toplevel_state_changed(self, _toplevel, _state: Gdk.ToplevelState) -> None:
        self._on_wm_geometry_changed(self, None)

    def _on_wm_geometry_changed(self, _window, _pspec) -> None:
        if self._mode != MODE_NORMAL:
            return
        was_managed = getattr(self, "_wm_geometry_managed", False)
        managed = self._is_wm_managed_geometry()
        self._wm_geometry_managed = managed
        self._sync_normal_size_constraints()
        if was_managed and not managed:
            self._maybe_lock_aspect()

    def _is_tiled(self) -> bool:
        toplevel = _window_toplevel(self)
        if toplevel is None:
            return False
        return is_tiled_toplevel_state(int(toplevel.get_state()))

    def _is_wm_managed_geometry(self) -> bool:
        """True when the compositor owns size/position (tile, maximize, fullscreen)."""
        if self._mode == MODE_FULLSCREEN:
            return True
        if self._mode != MODE_NORMAL:
            return False
        return self.is_maximized() or self._is_tiled()

    def _sync_normal_size_constraints(self) -> None:
        if self._mode == MODE_NORMAL and not self._is_wm_managed_geometry():
            min_w, min_h = min_normal_window_size()
            self.set_size_request(min_w, min_h)
        else:
            self.set_size_request(-1, -1)

    def _restore_normal_geometry(self, geo: SavedGeometry, *, initial: bool = False) -> None:
        """Apply saved normal-mode size/position without fighting WM tile/maximize."""
        _ = initial
        if geo.x < 0 or geo.y < 0:
            geo = compute_initial_geometry(self.get_display())
        self.set_default_size(geo.width, geo.height)
        if self._is_wm_managed_geometry():
            return
        if geo.maximized:
            self.maximize()
            return
        self.unmaximize()
        nx, ny = geo.x, geo.y
        GLib.idle_add(lambda x=nx, y=ny: (window_move(self, x, y), False)[1])

    def _on_size_changed(self, _window, pspec) -> None:
        if pspec.name == "width":
            self._maybe_lock_aspect()
        self._schedule_geometry_save()

    def _maybe_lock_aspect(self) -> None:
        if (
            not self._config.window.aspect_ratio_locked
            or self._mode != MODE_NORMAL
            or self._is_wm_managed_geometry()
            or self._aspect_adjusting
        ):
            return
        width = self.get_width()
        if width <= 0:
            return
        target_h = window_height_for_width(width)
        if abs(self.get_height() - target_h) > 2:
            self._aspect_adjusting = True
            self.resize(width, target_h)
            self._aspect_adjusting = False

    def _schedule_geometry_save(self) -> None:
        if not self._config.window.remember_geometry:
            return
        if self._save_timer is not None:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(
            self._config.window.geometry_save_debounce_ms,
            self._persist_geometry,
        )

    def _persist_geometry(self) -> bool:
        self._save_timer = None
        if self._mode == MODE_FULLSCREEN:
            return False
        wm_managed = self._mode == MODE_NORMAL and self._is_wm_managed_geometry()
        geo = SavedGeometry(
            width=self.get_width(),
            height=self.get_height(),
            x=window_xy(self)[0],
            y=window_xy(self)[1],
            maximized=self.is_maximized() if self._mode == MODE_NORMAL else False,
        )
        if self._mode == MODE_PIP and self._pip_window is not None:
            self._state["pip"] = geo
        elif self._mode == MODE_NORMAL:
            if wm_managed:
                geo = replace(geo, maximized=False)
            self._state["normal"] = geo
        self._state["last_mode"] = self._mode
        save_window_state(self._state)
        return False

    def _on_focus_changed(self, *_args) -> None:
        if not self.is_active():
            if self._config.input.release_on_unfocus:
                self.set_input_mode(InputMode.LOCAL, source="unfocus")
        else:
            self._video.update_input_mode(self._input_mode)
            self._check_hotplug()

    def toggle_pip(self) -> None:
        if self._mode == MODE_PIP:
            self._exit_pip()
        else:
            self._enter_pip()

    def _enter_pip(self) -> None:
        if self._mode == MODE_FULLSCREEN:
            self.unfullscreen()
        if self._mode != MODE_PIP:
            self._saved_normal = SavedGeometry(
                self.get_width(),
                self.get_height(),
                *window_xy(self),
                self.is_maximized(),
            )
            if self._mode == MODE_NORMAL:
                self._persist_geometry()

        self._mode = MODE_PIP
        self._state["last_mode"] = MODE_PIP
        self._compact_header.set_pip_active(True)

        pip_cfg = self._config.window.pip
        opacity = clamp_pip_opacity(self._state.get("pip_opacity", pip_cfg.opacity))
        keep_above = self._state.get("pip_keep_above", pip_cfg.keep_above_default)

        self._pip_window = PipWindow(
            self,
            geometry=self._state["pip"],
            opacity=opacity,
            keep_above=keep_above,
            corner=self._state.get("pip_corner", pip_cfg.corner),
            pip_cfg=pip_cfg,
            on_restore=self._exit_pip,
            on_geometry_changed=self._on_pip_geometry_changed,
            on_keep_above_changed=self._on_pip_keep_above_changed,
            on_opacity_changed=self._on_pip_opacity_changed,
        )
        self._capture.attach_video_widget(self._pip_window.picture)
        self._pip_window.set_status(self._video_status_text())
        self._pip_window.present()
        self.hide()
        save_window_state(self._state)

    def _exit_pip(self, *, restore_main: bool = True) -> None:
        if self._pip_window is not None:
            self._pip_window.destroy_window()
            self._pip_window = None

        self._capture.attach_video_widget(self._video.video_host)
        self._mode = MODE_NORMAL
        self._state["last_mode"] = MODE_NORMAL
        self._compact_header.set_pip_active(False)

        if not restore_main:
            return

        self.set_opacity(1.0)
        self._chrome.set_enabled(self._config.window.chrome_auto_hide)
        geo = self._saved_normal or self._state["normal"]
        self._restore_normal_geometry(geo, initial=False)
        self._sync_normal_size_constraints()
        self.present()
        self._video.set_status(self._video_status_text())
        if self._config.window.chrome_auto_hide:
            GLib.idle_add(lambda: (self._chrome.schedule_hide(), False)[1])
        save_window_state(self._state)

    def _on_pip_geometry_changed(self, geo: SavedGeometry) -> None:
        self._state["pip"] = geo
        save_window_state(self._state)

    def _on_pip_keep_above_changed(self, keep_above: bool) -> None:
        self._state["pip_keep_above"] = keep_above
        save_window_state(self._state)

    def _on_pip_opacity_changed(self, opacity: float) -> None:
        self._state["pip_opacity"] = opacity
        save_window_state(self._state)

    def _video_status_text(self) -> str | None:
        overlay = self._video._status_overlay
        if overlay.get_visible():
            return overlay.get_text()
        return None

    def toggle_fullscreen(self) -> None:
        if self._mode == MODE_FULLSCREEN:
            self._apply_mode(MODE_NORMAL)
        else:
            self._apply_mode(MODE_FULLSCREEN)

    def _apply_mode(self, mode: str, initial: bool = False) -> None:
        if mode == MODE_PIP:
            if initial:
                self._enter_pip()
            return

        if self._mode == MODE_PIP and mode != MODE_PIP:
            self._exit_pip()

        if not initial and mode != MODE_FULLSCREEN:
            self._persist_geometry()

        self._mode = mode
        self._state["last_mode"] = mode

        if mode == MODE_FULLSCREEN:
            self._chrome.cancel()
            self._headerbar.set_visible(False)
            self._control_shell.force_show()
            self._chrome.set_enabled(self._config.window.chrome_auto_hide)
            self.fullscreen()
            if not initial and self._config.window.chrome_auto_hide:
                GLib.idle_add(lambda: (self._chrome.schedule_hide(), False)[1])
            return

        self.unfullscreen()
        self._headerbar.set_visible(True)
        self._control_shell.force_show()
        self._chrome.set_enabled(self._config.window.chrome_auto_hide)
        self.set_opacity(1.0)
        self._sync_normal_size_constraints()
        self._restore_normal_geometry(self._state["normal"], initial=initial)
        if not initial and self._config.window.chrome_auto_hide:
            GLib.idle_add(lambda: (self._chrome.schedule_hide(), False)[1])

        if not initial:
            save_window_state(self._state)


class AndroidTvApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self._config = load_config()
        self._window: MainWindow | None = None
        self._creating_window = False
        self._diagnostics = DiagnosticsServer(self._diagnostics_backend)
        self.mpris = CaptureMprisController()
        self.connect("activate", self._on_activate)

    def _diagnostics_backend(self) -> AppDiagnosticsBackend | None:
        window = self._window
        if window is None:
            return None
        return window._diagnostics_backend

    def _on_window_destroy(self, *_args) -> None:
        note_user_quit()
        self.mpris.stop()
        self._diagnostics.stop()
        self._window = None
        self.quit()

    def _on_quit_requested(self, *_args) -> None:
        note_user_quit()
        if self._window is not None and self._window._settings_dialog is not None:
            self._window._settings_dialog.close()
        self.mpris.stop()
        self._diagnostics.stop()
        self.quit()

    def _on_activate(self, _app: Adw.Application) -> None:
        if self._window is not None:
            if self._window._pip_window is not None:
                self._window._pip_window.present()
            else:
                self._window.present()
            return
        if self._creating_window:
            return
        self._creating_window = True
        try:
            self._window = MainWindow(self, self._config)
            self._window.connect("destroy", self._on_window_destroy)
            self._window.present()
        except Exception:
            LOG.exception("Failed to open main window")
            self.quit()
        finally:
            self._creating_window = False

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self._diagnostics.start()
        self.mpris.start()

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_quit_requested)
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q", "<Primary>q"])

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect(
            "activate",
            lambda *_: show_about_dialog(self._window),
        )
        self.add_action(about_action)

        css = Gtk.CssProvider()
        css.load_from_data(
            b"""
            .status-ok { color: #2ec27e; }
            .status-bad { color: #e01b24; }
            .remote-armed { outline: 2px solid #3584e4; outline-offset: -2px; }
            .control-active { outline: 3px solid #2ec27e; outline-offset: -3px; }
            .control-banner, .hint-banner {
              background: alpha(black, 0.72);
              color: white;
              padding: 6px 10px;
              border-radius: 8px;
            }
            .hint-banner { background: alpha(black, 0.45); }
            .status-chip {
              background: alpha(white, 0.06);
              border-radius: 10px;
              padding: 2px 8px;
            }
            .status-chip-btn {
              border-radius: 10px;
              padding: 0;
            }
            .status-chip-btn:hover {
              background: alpha(white, 0.12);
            }
            .mode-chip {
              background: alpha(white, 0.04);
              border-radius: 10px;
              padding: 2px 8px;
            }
            .control-overlay {
              background: alpha(black, 0.78);
              border-radius: 14px;
              padding: 6px 10px;
            }
            .pip-window {
              background: black;
            }
            .pip-controls {
              margin: 6px;
            }
            .pip-controls-bar {
              background: alpha(black, 0.62);
              border-radius: 8px;
              padding: 2px 6px;
            }
            .pip-opacity-box {
              padding: 0 2px;
            }
            .pip-opacity-label {
              color: alpha(white, 0.78);
              font-size: 0.72rem;
              min-width: 2.2em;
            }
            .pip-opacity-scale {
              margin: 0 2px;
            }
            .pip-opacity-scale trough {
              background: alpha(white, 0.14);
              border-radius: 999px;
              min-height: 4px;
            }
            .pip-opacity-scale highlight {
              background: alpha(white, 0.52);
              border-radius: 999px;
            }
            .pip-opacity-scale slider {
              min-width: 12px;
              min-height: 12px;
              background: alpha(white, 0.95);
              border-radius: 999px;
            }
            .pip-controls-sep {
              margin: 4px 2px;
              background: alpha(white, 0.18);
              min-width: 1px;
            }
            .pip-control-btn {
              min-width: 28px;
              min-height: 28px;
              padding: 2px;
              border-radius: 6px;
              color: alpha(white, 0.92);
            }
            .pip-control-btn:hover {
              background: alpha(white, 0.16);
            }
            .pip-control-btn:active {
              background: alpha(white, 0.24);
            }
            .pip-status {
              background: alpha(black, 0.65);
              padding: 6px 10px;
              border-radius: 8px;
            }
            .control-expand-pill {
              background: alpha(black, 0.72);
              border-radius: 999px;
              padding: 4px 12px;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
