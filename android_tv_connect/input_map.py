"""Extended key mapping and passthrough helpers."""

from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk

from .adb_client import (
    KEYCODE_APP_SWITCH,
    KEYCODE_BACK,
    KEYCODE_DEL,
    KEYCODE_DPAD_DOWN,
    KEYCODE_DPAD_LEFT,
    KEYCODE_DPAD_RIGHT,
    KEYCODE_DPAD_UP,
    KEYCODE_ENTER,
    KEYCODE_HOME,
    KEYCODE_MEDIA_PLAY_PAUSE,
    KEYCODE_SETTINGS,
    KEYCODE_VOLUME_DOWN,
    KEYCODE_VOLUME_MUTE,
    KEYCODE_VOLUME_UP,
)

# Remote mode: navigation and TV essentials. Esc is never sent (handled locally).
NAVIGATION_KEYS: dict[int, int] = {
    Gdk.KEY_Up: KEYCODE_DPAD_UP,
    Gdk.KEY_Down: KEYCODE_DPAD_DOWN,
    Gdk.KEY_Left: KEYCODE_DPAD_LEFT,
    Gdk.KEY_Right: KEYCODE_DPAD_RIGHT,
    Gdk.KEY_Return: KEYCODE_ENTER,
    Gdk.KEY_KP_Enter: KEYCODE_ENTER,
    Gdk.KEY_BackSpace: KEYCODE_DEL,
    Gdk.KEY_space: KEYCODE_MEDIA_PLAY_PAUSE,
    Gdk.KEY_Delete: KEYCODE_DEL,
    Gdk.KEY_Home: KEYCODE_HOME,
    Gdk.KEY_Page_Up: KEYCODE_DPAD_UP,
    Gdk.KEY_Page_Down: KEYCODE_DPAD_DOWN,
}

# Full keyboard capture: navigation plus typing helpers and system keys.
KEY_MAP: dict[int, int] = {
    **NAVIGATION_KEYS,
    Gdk.KEY_Escape: KEYCODE_BACK,
    Gdk.KEY_Super_L: KEYCODE_HOME,
    Gdk.KEY_Super_R: KEYCODE_HOME,
    Gdk.KEY_Tab: KEYCODE_DPAD_RIGHT,
    Gdk.KEY_Menu: KEYCODE_SETTINGS,
    Gdk.KEY_AudioRaiseVolume: KEYCODE_VOLUME_UP,
    Gdk.KEY_AudioLowerVolume: KEYCODE_VOLUME_DOWN,
    Gdk.KEY_AudioMute: KEYCODE_VOLUME_MUTE,
}

PASSTHROUGH_EXTRA: dict[int, int] = {
    Gdk.KEY_Insert: KEYCODE_APP_SWITCH,
    Gdk.KEY_End: KEYCODE_DPAD_DOWN,
    Gdk.KEY_F1: KEYCODE_SETTINGS,
}


def printable_key(keyval: int) -> str | None:
    if Gdk.KEY_a <= keyval <= Gdk.KEY_z:
        return chr(keyval)
    if Gdk.KEY_A <= keyval <= Gdk.KEY_Z:
        return chr(keyval).lower()
    if Gdk.KEY_0 <= keyval <= Gdk.KEY_9:
        return chr(keyval)
    punctuation = {
        Gdk.KEY_period: ".",
        Gdk.KEY_comma: ",",
        Gdk.KEY_minus: "-",
        Gdk.KEY_equal: "=",
        Gdk.KEY_slash: "/",
        Gdk.KEY_at: "@",
        Gdk.KEY_apostrophe: "'",
        Gdk.KEY_semicolon: ";",
    }
    return punctuation.get(keyval)


def lookup_key(keyval: int, *, passthrough: bool) -> int | None:
    if keyval in KEY_MAP:
        return KEY_MAP[keyval]
    if passthrough and keyval in PASSTHROUGH_EXTRA:
        return PASSTHROUGH_EXTRA[keyval]
    return None
