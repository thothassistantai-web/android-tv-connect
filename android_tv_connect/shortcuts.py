"""Application keyboard shortcuts (Shift+F-keys by default)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk

# GNOME/Linux commonly reserve bare F1, F3, F5, F10, F11, F12.
# Defaults use Shift+F1–F6 to stay out of the way.
SHORTCUT_HELP = (
    "Use Shift+F1 through Shift+F12. Do not use Ctrl, Super, or Alt — "
    "those conflict with the desktop."
)

SHORTCUT_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("pip_toggle", "Toggle Picture-in-Picture", "<Shift>F1"),
    ("fullscreen_toggle", "Toggle fullscreen", "<Shift>F2"),
    ("release_control", "Release control (local mode)", "<Shift>F3"),
    ("keyboard_capture_toggle", "Toggle keyboard capture", "<Shift>F4"),
    ("mouse_mode_toggle", "Toggle mouse mode", "<Shift>F5"),
    ("open_settings", "Open settings", "<Shift>F6"),
    ("control_bar_toggle", "Toggle remote bar", "<Shift>F7"),
    ("mirror_toggle", "Toggle scrcpy mirror", "<Shift>F8"),
)

# Bare function keys often handled by the OS/compositor.
_RESERVED_BARE_FKEYS = frozenset({1, 3, 5, 10, 11, 12})


@dataclass(frozen=True)
class ShortcutsConfig:
    pip_toggle: str = "<Shift>F1"
    fullscreen_toggle: str = "<Shift>F2"
    release_control: str = "<Shift>F3"
    keyboard_capture_toggle: str = "<Shift>F4"
    mouse_mode_toggle: str = "<Shift>F5"
    open_settings: str = "<Shift>F6"
    control_bar_toggle: str = "<Shift>F7"
    mirror_toggle: str = "<Shift>F8"

    def get(self, action_id: str) -> str:
        return getattr(self, action_id)

    def with_action(self, action_id: str, value: str) -> "ShortcutsConfig":
        from dataclasses import replace

        return replace(self, **{action_id: value})


def humanize_shortcut(spec: str) -> str:
    """Turn '<Shift>F1' into 'Shift+F1' for UI labels."""
    return (
        spec.replace("<Shift>", "Shift+")
        .replace("<Primary>", "Ctrl+")
        .replace("<Alt>", "Alt+")
        .replace("<Super>", "Super+")
        .strip()
    )


def validate_shortcut(spec: str) -> tuple[bool, str]:
    spec = spec.strip()
    if not spec:
        return False, "Shortcut cannot be empty."

    lowered = spec.lower()
    if any(tok in lowered for tok in ("primary", "control", "ctrl", "super", "alt", "meta")):
        return False, "Use Shift+F-keys only — no Ctrl, Super, or Alt."

    if "shift" not in lowered:
        return False, "Application shortcuts must include Shift."

    match = re.search(r"[Ff](\d{1,2})\b", spec)
    if not match:
        return False, "Shortcut must include an F-key (F1–F12)."

    fnum = int(match.group(1))
    if fnum < 1 or fnum > 12:
        return False, "F-key must be between F1 and F12."

    trigger = Gtk.ShortcutTrigger.parse_string(spec)
    if trigger is None:
        return False, f"Could not parse shortcut: {spec!r}"

    if re.fullmatch(r"[Ff]\d{1,2}", spec.strip()) and fnum in _RESERVED_BARE_FKEYS:
        return False, f"F{fnum} is usually reserved by the OS — use Shift+F{fnum}."

    return True, ""


def key_event_matches(keyval: int, state: Gdk.ModifierType, spec: str) -> bool:
    trigger = Gtk.ShortcutTrigger.parse_string(spec.strip())
    if trigger is None:
        return False
    try:
        return bool(trigger.trigger(Gtk.ShortcutTriggerMatchFlags.NONE, keyval, state))
    except (AttributeError, TypeError):
        return _manual_match(keyval, state, spec)


def _manual_match(keyval: int, state: Gdk.ModifierType, spec: str) -> bool:
    match = re.search(r"[Ff](\d{1,2})\b", spec)
    if not match:
        return False
    fnum = int(match.group(1))
    expected = getattr(Gdk, f"KEY_F{fnum}", None)
    if expected is None or keyval != expected:
        return False
    shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
    if "shift" in spec.lower() and not shift:
        return False
    if state & (
        Gdk.ModifierType.CONTROL_MASK
        | Gdk.ModifierType.SUPER_MASK
        | Gdk.ModifierType.ALT_MASK
        | Gdk.ModifierType.META_MASK
    ):
        return False
    return True


def migrate_shortcuts(raw: dict) -> ShortcutsConfig:
    """Build shortcuts config from saved data or legacy input fields."""
    defaults = ShortcutsConfig()
    kwargs = {}
    for action_id, _label, default in SHORTCUT_DEFINITIONS:
        if action_id in raw:
            kwargs[action_id] = raw[action_id]
        else:
            kwargs[action_id] = default

    if "pip_toggle" not in raw and raw.get("pip_toggle_shortcut"):
        kwargs["pip_toggle"] = raw["pip_toggle_shortcut"]
    if "release_control" not in raw and raw.get("keyboard_release_shortcut"):
        kwargs["release_control"] = raw["keyboard_release_shortcut"]

    cfg = ShortcutsConfig(**kwargs)

    # Upgrade legacy Ctrl+Shift combos to Shift+F defaults when detected.
    legacy_markers = ("Primary", "Control", "Ctrl", "Escape", "p>")
    upgraded = {}
    for action_id, _label, default in SHORTCUT_DEFINITIONS:
        value = cfg.get(action_id)
        if any(marker.lower() in value.lower() for marker in legacy_markers):
            upgraded[action_id] = default
    if upgraded:
        from dataclasses import replace

        cfg = replace(cfg, **upgraded)
    return cfg


def bind_shortcuts(
    window: Gtk.Widget,
    shortcuts: ShortcutsConfig,
    actions: dict[str, Callable[[], None]],
) -> Gtk.ShortcutController:
    ctrl = Gtk.ShortcutController()
    ctrl.set_scope(Gtk.ShortcutScope.LOCAL)

    for action_id, _label, _default in SHORTCUT_DEFINITIONS:
        spec = shortcuts.get(action_id)
        fn = actions.get(action_id)
        if not fn:
            continue
        trigger = Gtk.ShortcutTrigger.parse_string(spec)
        if trigger is None:
            continue
        action = Gtk.CallbackAction.new(
            lambda *_args, callback=fn: (callback(), True)[1]
        )
        ctrl.add_shortcut(Gtk.Shortcut.new(trigger, action))

    window.add_controller(ctrl)
    return ctrl
