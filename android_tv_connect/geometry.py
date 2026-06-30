"""Window geometry persistence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import CONFIG_DIR, WindowConfig, default_config


@dataclass
class SavedGeometry:
    width: int
    height: int
    x: int
    y: int
    maximized: bool = False
    monitor: int = 0


WINDOW_STATE_PATH = CONFIG_DIR / "window.json"

PIP_OPACITY_MIN = 0.25
PIP_OPACITY_MAX = 1.0

HEADER_HEIGHT = 40
VIDEO_ASPECT = 16 / 9
MIN_NORMAL_WIDTH = 320
MIN_VIDEO_HEIGHT = 180

# Gdk.ToplevelState tiling flags (GDK 4; stable bit positions).
_TOPLEVEL_STATE_TILED = 1 << 4
_TOPLEVEL_STATE_LEFT_TILED = 1 << 5
_TOPLEVEL_STATE_RIGHT_TILED = 1 << 6
_TOPLEVEL_STATE_TOP_TILED = 1 << 7
_TOPLEVEL_STATE_BOTTOM_TILED = 1 << 8
TILED_TOPLEVEL_STATE_MASK = (
    _TOPLEVEL_STATE_TILED
    | _TOPLEVEL_STATE_LEFT_TILED
    | _TOPLEVEL_STATE_RIGHT_TILED
    | _TOPLEVEL_STATE_TOP_TILED
    | _TOPLEVEL_STATE_BOTTOM_TILED
)


def is_tiled_toplevel_state(state: int) -> bool:
    """Return True when any GDK top-level tiling flag is set."""
    return bool(state & TILED_TOPLEVEL_STATE_MASK)


def min_normal_window_size(header: int = HEADER_HEIGHT) -> tuple[int, int]:
    """Smallest normal-mode size that still shows a usable video area."""
    return MIN_NORMAL_WIDTH, window_height_for_width(MIN_NORMAL_WIDTH, header)


def video_area_height(width: int) -> int:
    return max(MIN_VIDEO_HEIGHT, int(width / VIDEO_ASPECT))


def window_height_for_width(width: int, header: int = HEADER_HEIGHT) -> int:
    return video_area_height(width) + header


def compute_initial_geometry(display) -> SavedGeometry:
    """Size window to ~58% monitor width at 16:9 (+ header) on first launch."""
    monitor = display.get_monitors().get_item(0) if display else None
    if monitor is not None:
        mg = monitor.get_geometry()
        width = min(1280, max(640, int(mg.width * 0.58)))
        x = mg.x + max(0, (mg.width - width) // 2)
        y = mg.y + max(0, (mg.height - window_height_for_width(width)) // 4)
        return SavedGeometry(
            width=width,
            height=window_height_for_width(width),
            x=x,
            y=y,
            maximized=False,
            monitor=0,
        )
    return SavedGeometry(1120, window_height_for_width(1120), -1, -1, False, 0)


def _geometry_to_dict(geo: SavedGeometry) -> dict[str, Any]:
    return asdict(geo)


def _dict_to_geometry(data: dict[str, Any], defaults: SavedGeometry) -> SavedGeometry:
    return SavedGeometry(
        width=int(data.get("width", defaults.width)),
        height=int(data.get("height", defaults.height)),
        x=int(data.get("x", defaults.x)),
        y=int(data.get("y", defaults.y)),
        maximized=bool(data.get("maximized", defaults.maximized)),
        monitor=int(data.get("monitor", defaults.monitor)),
    )


def load_window_state() -> dict[str, Any]:
    cfg = default_config().window
    defaults = {
        "last_mode": cfg.last_mode,
        "normal": SavedGeometry(
            cfg.normal.width,
            cfg.normal.height,
            cfg.normal.x,
            cfg.normal.y,
            cfg.normal.maximized,
            cfg.normal.monitor,
        ),
        "pip": SavedGeometry(
            cfg.pip.width,
            cfg.pip.height,
            cfg.pip.x,
            cfg.pip.y,
            False,
            cfg.pip.monitor,
        ),
        "pip_corner": cfg.pip.corner,
        "pip_opacity": cfg.pip.opacity,
        "pip_keep_above": cfg.pip.keep_above_default,
        "fullscreen_monitor": cfg.fullscreen.monitor,
    }
    if not WINDOW_STATE_PATH.exists():
        return defaults

    try:
        raw = json.loads(WINDOW_STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return defaults

    return {
        "last_mode": raw.get("last_mode", defaults["last_mode"]),
        "normal": _dict_to_geometry(raw.get("normal", {}), defaults["normal"]),
        "pip": _dict_to_geometry(raw.get("pip", {}), defaults["pip"]),
        "pip_corner": raw.get("pip_corner", defaults["pip_corner"]),
        "pip_opacity": clamp_pip_opacity(
            float(raw.get("pip_opacity", defaults["pip_opacity"]))
        ),
        "pip_keep_above": bool(raw.get("pip_keep_above", defaults["pip_keep_above"])),
        "fullscreen_monitor": int(
            raw.get("fullscreen_monitor", defaults["fullscreen_monitor"])
        ),
    }


def save_window_state(state: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_mode": state["last_mode"],
        "normal": _geometry_to_dict(state["normal"]),
        "pip": _geometry_to_dict(state["pip"]),
        "pip_corner": state.get("pip_corner", "bottom-right"),
        "pip_opacity": state.get("pip_opacity", 1.0),
        "pip_keep_above": state.get("pip_keep_above", True),
        "fullscreen_monitor": state.get("fullscreen_monitor", 0),
    }
    WINDOW_STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def clamp_pip_opacity(value: float) -> float:
    """Keep PiP opacity within the visible range (25%–100%)."""
    return max(PIP_OPACITY_MIN, min(PIP_OPACITY_MAX, float(value)))


def window_xy(window) -> tuple[int, int]:
    """Return window position, or (0, 0) when unavailable."""
    get_x = getattr(window, "get_x", None)
    get_y = getattr(window, "get_y", None)
    if callable(get_x) and callable(get_y):
        return int(get_x()), int(get_y())
    return 0, 0


def window_move(window, x: int, y: int) -> None:
    """Move a Gtk.Window when the toolkit exposes move()."""
    move = getattr(window, "move", None)
    if callable(move):
        move(x, y)


def pip_corner_position(
    corner: str,
    width: int,
    height: int,
    monitor_geo,
    margin: int = 16,
) -> tuple[int, int]:
    x = monitor_geo.x + margin
    y = monitor_geo.y + margin
    if "right" in corner:
        x = monitor_geo.x + monitor_geo.width - width - margin
    if "bottom" in corner:
        y = monitor_geo.y + monitor_geo.height - height - margin
    return x, y
