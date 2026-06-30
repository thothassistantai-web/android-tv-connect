"""Borderless Picture-in-Picture window — video only, movable and resizable."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from .config import PipWindowConfig
from .geometry import (
    PIP_OPACITY_MAX,
    PIP_OPACITY_MIN,
    SavedGeometry,
    clamp_pip_opacity,
    pip_corner_position,
    window_move,
    window_xy,
)
from .shortcuts import bind_shortcuts, humanize_shortcut

if TYPE_CHECKING:
    from .window import MainWindow

LOG = logging.getLogger(__name__)

_CONTROLS_HIDE_DELAY_MS = 600
_DRAG_THRESHOLD_PX = 8

_OPACITY_PRESETS: tuple[tuple[str, float], ...] = (
    ("25%", PIP_OPACITY_MIN),
    ("50%", 0.5),
    ("75%", 0.75),
    ("100%", PIP_OPACITY_MAX),
)


def _window_toplevel(window: Gtk.Window) -> Gdk.Toplevel | None:
    surface = window.get_surface()
    if isinstance(surface, Gdk.Toplevel):
        return surface
    return None


def set_window_layering(
    window: Gtk.Window,
    *,
    above: bool = False,
    below: bool = False,
) -> None:
    """Toggle always-on-top / send-to-back via the Gdk surface."""
    surface = window.get_surface()
    if surface is None:
        return
    set_above = getattr(surface, "set_keep_above", None)
    set_below = getattr(surface, "set_keep_below", None)
    if callable(set_above):
        set_above(above)
    if callable(set_below):
        set_below(below)


def apply_borderless(window: Gtk.Window) -> None:
    """Strip server-side decorations so PiP is video edge-to-edge."""
    window.set_decorated(False)
    toplevel = _window_toplevel(window)
    if toplevel is not None:
        toplevel.set_decorated(False)


def click_after_drag(offset_x: float, offset_y: float, *, threshold: float = _DRAG_THRESHOLD_PX) -> bool:
    """Return True when a gesture release should count as a click, not a drag."""
    return abs(offset_x) < threshold and abs(offset_y) < threshold


class PipWindow(Gtk.Window):
    """Small borderless window that shows capture video only."""

    def __init__(
        self,
        host: MainWindow,
        *,
        geometry: SavedGeometry,
        opacity: float,
        keep_above: bool,
        corner: str,
        pip_cfg: PipWindowConfig,
        on_restore: Callable[[], None],
        on_geometry_changed: Callable[[SavedGeometry], None],
        on_keep_above_changed: Callable[[bool], None],
        on_opacity_changed: Callable[[float], None],
    ) -> None:
        super().__init__(application=host.get_application())
        self._host = host
        self._pip_cfg = pip_cfg
        self._on_restore = on_restore
        self._on_geometry_changed = on_geometry_changed
        self._on_keep_above_changed = on_keep_above_changed
        self._on_opacity_changed = on_opacity_changed
        self._corner = corner
        self._keep_above = keep_above
        self._send_below = False
        self._opacity = clamp_pip_opacity(opacity)
        self._controls_hide_timer: int | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._drag_offset: tuple[float, float] = (0.0, 0.0)
        self._save_timer: int | None = None
        self._menu_actions: Gio.SimpleActionGroup | None = None
        self._opacity_slider_syncing = False

        self.set_title("")
        self.set_resizable(True)
        self.set_deletable(True)
        self.set_icon_name(host.get_icon_name() or "video-display-symbolic")
        self.add_css_class("pip-window")
        self.set_opacity(self._opacity)
        self.set_size_request(pip_cfg.min_width, pip_cfg.min_height)
        self.set_default_size(geometry.width, geometry.height)
        apply_borderless(self)

        overlay = Gtk.Overlay()
        overlay.set_vexpand(True)
        overlay.set_hexpand(True)

        self._picture = Gtk.Picture()
        self._picture.set_vexpand(True)
        self._picture.set_hexpand(True)
        self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._picture.add_css_class("video-surface")
        overlay.set_child(self._picture)

        self._status_overlay = Gtk.Label(label="")
        self._status_overlay.add_css_class("dim-label")
        self._status_overlay.add_css_class("pip-status")
        self._status_overlay.set_valign(Gtk.Align.CENTER)
        self._status_overlay.set_halign(Gtk.Align.CENTER)
        self._status_overlay.set_visible(False)
        overlay.add_overlay(self._status_overlay)

        self._controls_revealer = Gtk.Revealer()
        self._controls_revealer.set_reveal_child(not pip_cfg.soft_bar_auto_hide)
        self._controls_revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self._controls_revealer.set_transition_duration(180)
        self._controls_revealer.set_valign(Gtk.Align.START)
        self._controls_revealer.set_halign(Gtk.Align.END)
        self._controls_revealer.add_css_class("pip-controls")

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        controls.add_css_class("pip-controls-bar")

        opacity_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        opacity_box.add_css_class("pip-opacity-box")
        self._opacity_slider = self._build_opacity_slider()
        self._opacity_label = Gtk.Label(label=self._opacity_percent_label())
        self._opacity_label.add_css_class("pip-opacity-label")
        opacity_box.append(self._opacity_slider)
        opacity_box.append(self._opacity_label)
        controls.append(opacity_box)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.add_css_class("pip-controls-sep")
        controls.append(sep)

        self._minimize_btn = self._control_btn(
            "window-minimize-symbolic",
            "Minimize",
            self._on_minimize,
        )
        self._maximize_btn = self._control_btn(
            "window-maximize-symbolic",
            "Maximize",
            self._on_maximize_toggle,
        )
        self._close_btn = self._control_btn(
            "window-close-symbolic",
            "Close PiP",
            self._on_close_clicked,
        )
        controls.append(self._minimize_btn)
        controls.append(self._maximize_btn)
        controls.append(self._close_btn)
        self._controls_revealer.set_child(controls)
        overlay.add_overlay(self._controls_revealer)

        self.set_child(overlay)

        self._install_video_gestures()
        self._install_shortcuts()

        self.connect("close-request", self._on_close_request)
        self.connect("realize", self._on_realize)
        self.connect("notify::width", self._on_size_changed)
        self.connect("notify::height", self._on_size_changed)
        self.connect("notify::maximized", self._on_maximized_changed)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_pointer_enter)
        motion.connect("leave", self._on_pointer_leave)
        self.add_controller(motion)

        self._apply_layering()
        self._place_initial(geometry)
        self._sync_maximize_icon()

    @property
    def picture(self) -> Gtk.Picture:
        return self._picture

    def set_status(self, text: str | None) -> None:
        if text:
            self._status_overlay.set_text(text)
            self._status_overlay.set_visible(True)
        else:
            self._status_overlay.set_visible(False)

    def update_opacity(self, opacity: float) -> None:
        self._set_opacity(clamp_pip_opacity(opacity), notify_host=False)

    def _opacity_percent_label(self) -> str:
        return f"{int(round(self._opacity * 100))}%"

    def _build_opacity_slider(self) -> Gtk.Scale:
        adjustment = Gtk.Adjustment.new(
            self._opacity,
            PIP_OPACITY_MIN,
            PIP_OPACITY_MAX,
            0.05,
            0.1,
            0.0,
        )
        scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=adjustment,
        )
        scale.set_draw_value(False)
        scale.set_has_origin(False)
        scale.set_size_request(72, -1)
        scale.set_tooltip_text("Window opacity")
        scale.add_css_class("pip-opacity-scale")
        scale.connect("value-changed", self._on_opacity_slider_changed)
        return scale

    def _sync_opacity_slider(self) -> None:
        self._opacity_slider_syncing = True
        self._opacity_slider.set_value(self._opacity)
        self._opacity_label.set_text(self._opacity_percent_label())
        self._opacity_slider_syncing = False

    def _on_opacity_slider_changed(self, scale: Gtk.Scale) -> None:
        if self._opacity_slider_syncing:
            return
        self._set_opacity(clamp_pip_opacity(scale.get_value()))

    def update_config(self, pip_cfg: PipWindowConfig) -> None:
        self._pip_cfg = pip_cfg
        self.set_size_request(pip_cfg.min_width, pip_cfg.min_height)
        if pip_cfg.soft_bar_auto_hide:
            self._schedule_controls_hide()
        else:
            self._cancel_controls_hide()
            self._controls_revealer.set_reveal_child(True)

    def _control_btn(self, icon: str, tip: str, handler) -> Gtk.Button:
        btn = Gtk.Button()
        btn.set_has_frame(False)
        btn.set_tooltip_text(tip)
        btn.set_child(Gtk.Image.new_from_icon_name(icon))
        btn.add_css_class("flat")
        btn.add_css_class("pip-control-btn")
        btn.set_size_request(28, 28)
        btn.connect("clicked", handler)
        return btn

    def _place_initial(self, geometry: SavedGeometry) -> None:
        display = self.get_display()
        monitor = display.get_monitors().get_item(geometry.monitor)
        if monitor is None:
            monitor = display.get_monitors().get_item(0)
        if geometry.x > 0 or geometry.y > 0:
            x, y = geometry.x, geometry.y
        elif monitor is not None:
            mg = monitor.get_geometry()
            x, y = pip_corner_position(
                self._corner,
                geometry.width,
                geometry.height,
                mg,
                self._pip_cfg.margin_px,
            )
        else:
            x, y = geometry.x, geometry.y
        GLib.idle_add(lambda x=x, y=y: (window_move(self, x, y), False)[1])

    def _on_realize(self, *_args) -> None:
        apply_borderless(self)

    def _install_video_gestures(self) -> None:
        drag = Gtk.GestureDrag()
        drag.set_button(0)
        drag.connect("drag-begin", self._on_video_drag_begin)
        drag.connect("drag-update", self._on_video_drag_update)
        drag.connect("drag-end", self._on_video_drag_end)
        self._picture.add_controller(drag)

        left = Gtk.GestureClick()
        left.set_button(1)
        left.connect("released", self._on_left_click)
        self._picture.add_controller(left)

        right = Gtk.GestureClick()
        right.set_button(3)
        right.connect("pressed", self._on_right_click)
        self._picture.add_controller(right)

    def _install_shortcuts(self) -> None:
        config = self._host.config
        actions = {
            "pip_toggle": self._on_restore,
        }
        bind_shortcuts(self, config.shortcuts, actions)
        tip = humanize_shortcut(config.shortcuts.pip_toggle)
        self.set_tooltip_text(f"Left-click to restore · {tip} to exit PiP")

    def _on_video_drag_begin(self, _gesture, _x: float, _y: float) -> None:
        self._drag_origin = window_xy(self)
        self._drag_offset = (0.0, 0.0)
        self._show_controls()

    def _on_video_drag_update(self, _gesture, offset_x: float, offset_y: float) -> None:
        self._drag_offset = (offset_x, offset_y)
        if self._drag_origin is None:
            return
        ox, oy = self._drag_origin
        window_move(self, ox + int(offset_x), oy + int(offset_y))

    def _on_video_drag_end(self, _gesture, _offset_x: float, _offset_y: float) -> None:
        self._drag_origin = None

    def _on_left_click(self, _gesture, _n_press: int, _x: float, _y: float) -> None:
        ox, oy = self._drag_offset
        if not click_after_drag(ox, oy):
            self._drag_offset = (0.0, 0.0)
            return
        self._drag_offset = (0.0, 0.0)
        self._on_restore()

    def _on_right_click(self, gesture, _n_press: int, x: float, y: float) -> None:
        self._show_context_menu(x, y)

    def _on_minimize(self, *_args) -> None:
        self.minimize()

    def _on_maximize_toggle(self, *_args) -> None:
        if self.is_maximized():
            self.unmaximize()
        else:
            self.maximize()

    def _on_close_clicked(self, *_args) -> None:
        self._on_restore()

    def _on_maximized_changed(self, *_args) -> None:
        self._sync_maximize_icon()

    def _sync_maximize_icon(self) -> None:
        image = self._maximize_btn.get_child()
        if not isinstance(image, Gtk.Image):
            return
        icon = "window-restore-symbolic" if self.is_maximized() else "window-maximize-symbolic"
        image.set_from_icon_name(icon)
        tip = "Restore" if self.is_maximized() else "Maximize"
        self._maximize_btn.set_tooltip_text(tip)

    def _show_context_menu(self, x: float, y: float) -> None:
        menu = Gio.Menu()

        layer_section = Gio.Menu()
        above_label = "✓ Always on top" if self._keep_above else "Always on top"
        below_label = "✓ Send to back" if self._send_below else "Send to back"
        layer_section.append(above_label, "pip.always-on-top")
        layer_section.append(below_label, "pip.send-to-back")
        menu.append_section(None, layer_section)

        opacity_menu = Gio.Menu()
        for label, value in _OPACITY_PRESETS:
            mark = "✓ " if abs(self._opacity - value) < 0.01 else ""
            opacity_menu.append(f"{mark}{label}", f"pip.opacity.{int(value * 100)}")
        menu.append_submenu("Opacity", opacity_menu)

        actions_section = Gio.Menu()
        actions_section.append("Restore window", "pip.restore")
        actions_section.append("Close PiP", "pip.close")
        menu.append_section(None, actions_section)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self._picture)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)

        action_group = Gio.SimpleActionGroup()
        action_group.add_action(Gio.SimpleAction.new("always-on-top", None))
        action_group.add_action(Gio.SimpleAction.new("send-to-back", None))
        action_group.add_action(Gio.SimpleAction.new("restore", None))
        action_group.add_action(Gio.SimpleAction.new("close", None))
        for _label, value in _OPACITY_PRESETS:
            action_group.add_action(
                Gio.SimpleAction.new(f"opacity.{int(value * 100)}", None),
            )

        action_group.lookup_action("always-on-top").connect(
            "activate", lambda *_: self._toggle_keep_above()
        )
        action_group.lookup_action("send-to-back").connect(
            "activate", lambda *_: self._toggle_send_below()
        )
        action_group.lookup_action("restore").connect(
            "activate", lambda *_: self._on_restore()
        )
        action_group.lookup_action("close").connect(
            "activate", lambda *_: self._on_restore()
        )
        for _label, value in _OPACITY_PRESETS:
            action_group.lookup_action(f"opacity.{int(value * 100)}").connect(
                "activate",
                lambda *_a, v=value: self._set_opacity(v),
            )

        self._menu_actions = action_group
        self.insert_action_group("pip", action_group)
        popover.connect("closed", self._on_menu_closed)
        popover.popup()

    def _on_menu_closed(self, *_args) -> None:
        self.insert_action_group("pip", None)
        self._menu_actions = None

    def _toggle_keep_above(self) -> None:
        self._set_layer_mode(above=not self._keep_above, below=False)

    def _toggle_send_below(self) -> None:
        self._set_layer_mode(above=False, below=not self._send_below)

    def _set_layer_mode(self, *, above: bool = False, below: bool = False) -> None:
        self._keep_above = above
        self._send_below = below
        if above:
            self._send_below = False
        if below:
            self._keep_above = False
        self._apply_layering()
        self._on_keep_above_changed(self._keep_above)

    def _set_opacity(
        self,
        opacity: float,
        *,
        notify_host: bool = True,
    ) -> None:
        clamped = clamp_pip_opacity(opacity)
        self._opacity = clamped
        self.set_opacity(clamped)
        self._sync_opacity_slider()
        if notify_host:
            self._on_opacity_changed(clamped)

    def _apply_layering(self) -> None:
        set_window_layering(
            self,
            above=self._keep_above,
            below=self._send_below,
        )

    def _on_close_request(self, *_args) -> bool:
        self._on_restore()
        return True

    def _on_size_changed(self, _window, _pspec) -> None:
        self._schedule_geometry_save()

    def _schedule_geometry_save(self) -> None:
        if self._save_timer is not None:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(500, self._persist_geometry)

    def _persist_geometry(self) -> bool:
        self._save_timer = None
        geo = SavedGeometry(
            width=self.get_width(),
            height=self.get_height(),
            x=window_xy(self)[0],
            y=window_xy(self)[1],
            maximized=self.is_maximized(),
            monitor=0,
        )
        self._on_geometry_changed(geo)
        return False

    def _show_controls(self) -> None:
        self._cancel_controls_hide()
        self._controls_revealer.set_reveal_child(True)

    def _on_pointer_enter(self, *_args) -> None:
        self._show_controls()

    def _on_pointer_leave(self, *_args) -> None:
        if self._pip_cfg.soft_bar_auto_hide:
            self._schedule_controls_hide()

    def _schedule_controls_hide(self) -> None:
        self._cancel_controls_hide()
        self._controls_hide_timer = GLib.timeout_add(
            _CONTROLS_HIDE_DELAY_MS,
            self._hide_controls,
        )

    def _cancel_controls_hide(self) -> None:
        if self._controls_hide_timer is not None:
            GLib.source_remove(self._controls_hide_timer)
            self._controls_hide_timer = None

    def _hide_controls(self) -> bool:
        self._controls_hide_timer = None
        if self._pip_cfg.soft_bar_auto_hide:
            self._controls_revealer.set_reveal_child(False)
        return False

    def destroy_window(self) -> None:
        self._cancel_controls_hide()
        if self._save_timer is not None:
            GLib.source_remove(self._save_timer)
            self._save_timer = None
        self._on_restore = lambda: None
        self.destroy()
