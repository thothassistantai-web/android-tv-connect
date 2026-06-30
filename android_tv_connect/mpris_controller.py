"""MPRIS media controls for HDMI capture playback (Cosmic/GNOME/playerctl)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from .branding import APP_NAME

LOG = logging.getLogger(__name__)

MPRIS_BUS_NAME = "org.mpris.MediaPlayer2.androidtvconnect"
MPRIS_OBJECT_PATH = "/org/mpris/MediaPlayer2"
MPRIS_ROOT_IFACE = "org.mpris.MediaPlayer2"
MPRIS_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
DESKTOP_ENTRY = "android-tv-connect"

PLAYBACK_PLAYING = "Playing"
PLAYBACK_PAUSED = "Paused"
PLAYBACK_STOPPED = "Stopped"

DEFAULT_STREAM_TITLE = "HDMI Capture"
DEFAULT_STREAM_ARTIST = "Onn Stick"

_MPRIS_XML = """
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="CanSetFullscreen" type="b" access="read"/>
    <property name="Fullscreen" type="b" access="readwrite"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Play"/>
    <method name="Stop"/>
    <method name="Seek">
      <arg direction="in" name="Offset" type="x"/>
    </method>
    <method name="SetPosition">
      <arg direction="in" name="TrackId" type="o"/>
      <arg direction="in" name="Position" type="x"/>
    </method>
    <method name="OpenUri">
      <arg direction="in" name="Uri" type="s"/>
    </method>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""


def capture_state_to_playback_status(state: str, *, user_paused: bool = False) -> str:
    """Map internal capture pipeline state to MPRIS PlaybackStatus."""
    if state == "playing":
        return PLAYBACK_PLAYING
    if state == "paused" or user_paused:
        return PLAYBACK_PAUSED
    return PLAYBACK_STOPPED


def build_metadata(
    title: str = DEFAULT_STREAM_TITLE,
    artist: str | None = DEFAULT_STREAM_ARTIST,
) -> dict[str, GLib.Variant]:
    """Build MPRIS Metadata property value."""
    metadata: dict[str, GLib.Variant] = {
        "mpris:trackid": GLib.Variant("o", f"{MPRIS_OBJECT_PATH}/track/1"),
        "xesam:title": GLib.Variant("s", title),
    }
    if artist:
        metadata["xesam:artist"] = GLib.Variant("as", [artist])
    return metadata


@dataclass
class MprisHandlers:
    """Callbacks invoked from the GLib main loop (via idle handlers)."""

    on_play: Callable[[], None] | None = None
    on_pause: Callable[[], None] | None = None
    on_raise: Callable[[], None] | None = None
    get_volume: Callable[[], float | None] | None = None
    set_volume: Callable[[float], bool] | None = None


@dataclass
class _MprisState:
    playback_status: str = PLAYBACK_STOPPED
    volume: float = 1.0
    metadata: dict[str, GLib.Variant] = field(default_factory=build_metadata)
    handlers: MprisHandlers = field(default_factory=MprisHandlers)


class CaptureMprisController:
    """Session-bus MPRIS player for system media overlays."""

    def __init__(self, identity: str = APP_NAME) -> None:
        self._identity = identity
        self._state = _MprisState()
        self._connection: Gio.DBusConnection | None = None
        self._owner_id: int = 0
        self._registrations: list[int] = []
        self._node_info = Gio.DBusNodeInfo.new_for_xml(_MPRIS_XML)
        self._iface_by_name = {
            iface.name: iface for iface in self._node_info.interfaces if iface.name
        }

    @property
    def available(self) -> bool:
        return self._connection is not None

    @property
    def playback_status(self) -> str:
        return self._state.playback_status

    def set_handlers(self, handlers: MprisHandlers) -> None:
        self._state.handlers = handlers
        volume = handlers.get_volume() if handlers.get_volume else None
        if volume is not None:
            self._state.volume = max(0.0, min(1.0, volume))
            self._emit_properties_changed(
                MPRIS_PLAYER_IFACE,
                {"Volume": GLib.Variant("d", self._state.volume)},
            )

    def clear_handlers(self) -> None:
        self._state.handlers = MprisHandlers()

    def start(self) -> None:
        if self._owner_id:
            return
        self._owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            MPRIS_BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            self._on_name_acquired,
            self._on_name_lost,
        )
        LOG.info("MPRIS registration requested (%s)", MPRIS_BUS_NAME)

    def stop(self) -> None:
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0
        self._unregister_object()
        self._connection = None
        LOG.info("MPRIS unregistered")

    def set_playback_status(self, status: str) -> None:
        if status not in (PLAYBACK_PLAYING, PLAYBACK_PAUSED, PLAYBACK_STOPPED):
            return
        if self._state.playback_status == status:
            return
        self._state.playback_status = status
        changed = {
            "PlaybackStatus": GLib.Variant("s", status),
            "CanPlay": GLib.Variant("b", status != PLAYBACK_PLAYING),
            "CanPause": GLib.Variant("b", status == PLAYBACK_PLAYING),
        }
        self._emit_properties_changed(MPRIS_PLAYER_IFACE, changed)

    def set_metadata(
        self,
        title: str = DEFAULT_STREAM_TITLE,
        artist: str | None = DEFAULT_STREAM_ARTIST,
    ) -> None:
        metadata = build_metadata(title, artist)
        self._state.metadata = metadata
        self._emit_properties_changed(
            MPRIS_PLAYER_IFACE,
            {"Metadata": GLib.Variant("a{sv}", metadata)},
        )

    def set_volume(self, volume: float) -> None:
        clamped = max(0.0, min(1.0, volume))
        if abs(self._state.volume - clamped) < 1e-6:
            return
        self._state.volume = clamped
        self._emit_properties_changed(
            MPRIS_PLAYER_IFACE,
            {"Volume": GLib.Variant("d", clamped)},
        )

    def sync_from_capture(self, state: str, *, user_paused: bool = False) -> None:
        self.set_playback_status(capture_state_to_playback_status(state, user_paused=user_paused))

    def _on_bus_acquired(
        self,
        connection: Gio.DBusConnection,
        _name: str,
        *_args: Any,
    ) -> None:
        self._connection = connection
        self._register_object(connection)

    def _on_name_acquired(self, _connection: Gio.DBusConnection, name: str, *_args: Any) -> None:
        LOG.info("MPRIS active on session bus as %s", name)

    def _on_name_lost(self, _connection: Gio.DBusConnection, name: str, *_args: Any) -> None:
        LOG.warning("MPRIS bus name lost: %s", name)
        self._unregister_object()
        self._connection = None

    def _register_object(self, connection: Gio.DBusConnection) -> None:
        self._unregister_object()
        for iface in self._node_info.interfaces:
            if not iface.name:
                continue
            reg_id = connection.register_object(
                MPRIS_OBJECT_PATH,
                iface,
                self._handle_method_call,
                self._handle_get_property,
                self._handle_set_property,
            )
            self._registrations.append(reg_id)

    def _unregister_object(self) -> None:
        if self._connection is None:
            self._registrations.clear()
            return
        for reg_id in self._registrations:
            try:
                self._connection.unregister_object(reg_id)
            except GLib.Error:
                pass
        self._registrations.clear()

    def _emit_properties_changed(
        self,
        interface_name: str,
        changed: dict[str, GLib.Variant],
    ) -> None:
        if self._connection is None or not changed:
            return
        self._connection.emit_signal(
            None,
            MPRIS_OBJECT_PATH,
            "org.freedesktop.DBus.Properties",
            "PropertiesChanged",
            GLib.Variant("(sa{sv}as)", (interface_name, changed, [])),
        )

    def _dispatch(self, callback: Callable[[], None] | None) -> None:
        if callback is None:
            return
        GLib.idle_add(self._run_callback, callback)

    @staticmethod
    def _run_callback(callback: Callable[[], None]) -> bool:
        try:
            callback()
        except Exception:
            LOG.exception("MPRIS callback failed")
        return False

    def _handle_method_call(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        interface_name: str,
        method_name: str,
        _parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        handlers = self._state.handlers
        try:
            if interface_name == MPRIS_ROOT_IFACE:
                if method_name == "Raise":
                    self._dispatch(handlers.on_raise)
                    invocation.return_value(None)
                    return
                if method_name == "Quit":
                    invocation.return_value(None)
                    return

            if interface_name == MPRIS_PLAYER_IFACE:
                if method_name in ("Next", "Previous", "Seek", "SetPosition", "OpenUri"):
                    invocation.return_value(None)
                    return
                if method_name == "Play":
                    self._dispatch(handlers.on_play)
                    invocation.return_value(None)
                    return
                if method_name == "Pause":
                    self._dispatch(handlers.on_pause)
                    invocation.return_value(None)
                    return
                if method_name == "PlayPause":
                    if self._state.playback_status == PLAYBACK_PLAYING:
                        self._dispatch(handlers.on_pause)
                    else:
                        self._dispatch(handlers.on_play)
                    invocation.return_value(None)
                    return
                if method_name == "Stop":
                    self._dispatch(handlers.on_pause)
                    invocation.return_value(None)
                    return

            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method_name)
        except Exception as exc:
            LOG.exception("MPRIS method %s failed", method_name)
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.Failed",
                str(exc),
            )

    def _root_properties(self) -> dict[str, GLib.Variant]:
        return {
            "CanQuit": GLib.Variant("b", False),
            "CanRaise": GLib.Variant("b", True),
            "CanSetFullscreen": GLib.Variant("b", False),
            "Fullscreen": GLib.Variant("b", False),
            "HasTrackList": GLib.Variant("b", False),
            "Identity": GLib.Variant("s", self._identity),
            "DesktopEntry": GLib.Variant("s", DESKTOP_ENTRY),
            "SupportedUriSchemes": GLib.Variant("as", []),
            "SupportedMimeTypes": GLib.Variant("as", []),
        }

    def _player_properties(self) -> dict[str, GLib.Variant]:
        playing = self._state.playback_status == PLAYBACK_PLAYING
        return {
            "PlaybackStatus": GLib.Variant("s", self._state.playback_status),
            "LoopStatus": GLib.Variant("s", "None"),
            "Rate": GLib.Variant("d", 1.0),
            "Shuffle": GLib.Variant("b", False),
            "Metadata": GLib.Variant("a{sv}", self._state.metadata),
            "Volume": GLib.Variant("d", self._state.volume),
            "Position": GLib.Variant("x", 0),
            "MinimumRate": GLib.Variant("d", 1.0),
            "MaximumRate": GLib.Variant("d", 1.0),
            "CanGoNext": GLib.Variant("b", False),
            "CanGoPrevious": GLib.Variant("b", False),
            "CanPlay": GLib.Variant("b", not playing),
            "CanPause": GLib.Variant("b", playing),
            "CanSeek": GLib.Variant("b", False),
            "CanControl": GLib.Variant("b", True),
        }

    def _handle_get_property(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        interface_name: str,
        property_name: str,
    ) -> GLib.Variant | None:
        props = self._properties_for_interface(interface_name)
        if props is None:
            return None
        return props.get(property_name)

    def _handle_set_property(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        interface_name: str,
        property_name: str,
        value: GLib.Variant,
    ) -> bool:
        if interface_name != MPRIS_PLAYER_IFACE:
            return False
        if property_name == "Volume":
            volume = float(value.get_double())
            clamped = max(0.0, min(1.0, volume))
            self._state.volume = clamped
            setter = self._state.handlers.set_volume
            if setter is not None:
                GLib.idle_add(self._run_volume_setter, setter, clamped)
            self._emit_properties_changed(
                MPRIS_PLAYER_IFACE,
                {"Volume": GLib.Variant("d", clamped)},
            )
            return True
        if property_name in ("LoopStatus", "Rate", "Shuffle", "Fullscreen"):
            return True
        return False

    @staticmethod
    def _run_volume_setter(setter: Callable[[float], bool], volume: float) -> bool:
        try:
            setter(volume)
        except Exception:
            LOG.exception("MPRIS volume setter failed")
        return False

    def _properties_for_interface(self, interface_name: str) -> dict[str, GLib.Variant] | None:
        if interface_name == MPRIS_ROOT_IFACE:
            return self._root_properties()
        if interface_name == MPRIS_PLAYER_IFACE:
            return self._player_properties()
        return None

    def get_property(self, interface_name: str, property_name: str) -> GLib.Variant | None:
        """Test helper: read a registered property without D-Bus."""
        props = self._properties_for_interface(interface_name)
        if props is None:
            return None
        return props.get(property_name)
