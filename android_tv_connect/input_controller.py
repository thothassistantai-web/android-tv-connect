"""Input focus and keyboard capture state machine."""

from __future__ import annotations

from enum import Enum

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk

from .input_map import NAVIGATION_KEYS, lookup_key, printable_key


class InputMode(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
    KEYBOARD = "keyboard"


def mode_allows_navigation(mode: InputMode) -> bool:
    return mode in (InputMode.REMOTE, InputMode.KEYBOARD)


def mode_allows_scroll(mode: InputMode) -> bool:
    return mode == InputMode.REMOTE


def mode_allows_pointer(mode: InputMode) -> bool:
    return mode in (InputMode.REMOTE, InputMode.KEYBOARD)


def is_soft_release(keyval: int, state: Gdk.ModifierType) -> bool:
    """Esc releases control without sending BACK (unless already typing)."""
    if keyval != Gdk.KEY_Escape:
        return False
    if state & (
        Gdk.ModifierType.CONTROL_MASK
        | Gdk.ModifierType.SHIFT_MASK
        | Gdk.ModifierType.ALT_MASK
    ):
        return False
    return True


def navigation_key(keyval: int) -> int | None:
    return NAVIGATION_KEYS.get(keyval)


def forward_key(
    adb,
    keyval: int,
    state: Gdk.ModifierType,
    mode: InputMode,
) -> bool:
    """Forward a key to Android based on mode. Return True if consumed."""
    if mode == InputMode.LOCAL:
        return False

    if mode == InputMode.KEYBOARD:
        if state & Gdk.ModifierType.CONTROL_MASK:
            return False
        mapped = lookup_key(keyval, passthrough=True)
        if mapped is not None:
            adb.keyevent(mapped)
            return True
        ch = printable_key(keyval)
        if ch:
            adb.text(ch)
            return True
        return False

    mapped = navigation_key(keyval)
    if mapped is not None:
        adb.keyevent(mapped)
        return True
    return False


MODE_LABELS = {
    InputMode.LOCAL: "Local",
    InputMode.REMOTE: "Controlling",
    InputMode.KEYBOARD: "Keyboard",
}


def mode_hint(mode: InputMode, release_label: str) -> str:
    if mode == InputMode.LOCAL:
        return "Click the video to control your Android TV"
    if mode == InputMode.REMOTE:
        return (
            f"Remote control — Esc to stop · {release_label} or ⌨ for full keyboard"
        )
    return f"Keyboard capture — {release_label} or Esc to stop"
