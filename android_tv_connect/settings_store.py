"""Load and save user settings."""

from __future__ import annotations

import json
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Any

from .config import (
    CONFIG_DIR,
    AdbConfig,
    AppConfig,
    CaptureConfig,
    InputConfig,
    WindowConfig,
    default_config,
)
from .adb_settings import normalize_adb_config
from .shortcuts import migrate_shortcuts

SETTINGS_PATH = CONFIG_DIR / "config.json"


def _merge_dataclass(cls, data: dict[str, Any] | None):
    base = cls()
    if not data:
        return base
    kwargs = {}
    for field in fields(cls):
        if field.name in data:
            kwargs[field.name] = data[field.name]
    return cls(**kwargs)


def load_config() -> AppConfig:
    defaults = default_config()
    if not SETTINGS_PATH.exists():
        return defaults

    try:
        raw = json.loads(SETTINGS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return defaults

    window_raw = raw.get("window", {})
    pip_raw = window_raw.get("pip", {})
    normal_raw = window_raw.get("normal", {})

    input_raw = dict(raw.get("input", {}))
    if "click_to_control" not in input_raw and input_raw.get(
        "auto_keyboard_capture_on_focus"
    ):
        input_raw["click_to_control"] = input_raw["auto_keyboard_capture_on_focus"]

    shortcuts_raw = dict(raw.get("shortcuts", {}))
    if not shortcuts_raw:
        shortcuts_raw = {
            k: v
            for k, v in input_raw.items()
            if k in ("pip_toggle_shortcut", "keyboard_release_shortcut")
        }
        if input_raw.get("pip_toggle_shortcut"):
            shortcuts_raw.setdefault("pip_toggle", input_raw["pip_toggle_shortcut"])
        if input_raw.get("keyboard_release_shortcut"):
            shortcuts_raw.setdefault(
                "release_control", input_raw["keyboard_release_shortcut"]
            )

    return AppConfig(
        adb=normalize_adb_config(_merge_dataclass(AdbConfig, raw.get("adb"))),
        capture=_merge_dataclass(CaptureConfig, raw.get("capture")),
        window=WindowConfig(
            last_mode=window_raw.get("last_mode", defaults.window.last_mode),
            remember_geometry=window_raw.get(
                "remember_geometry", defaults.window.remember_geometry
            ),
            aspect_ratio_locked=window_raw.get(
                "aspect_ratio_locked", defaults.window.aspect_ratio_locked
            ),
            geometry_save_debounce_ms=window_raw.get(
                "geometry_save_debounce_ms",
                defaults.window.geometry_save_debounce_ms,
            ),
            chrome_auto_hide=window_raw.get(
                "chrome_auto_hide", defaults.window.chrome_auto_hide
            ),
            chrome_hide_delay_ms=int(
                window_raw.get(
                    "chrome_hide_delay_ms",
                    defaults.window.chrome_hide_delay_ms,
                )
            ),
            banner_auto_hide_ms=int(
                window_raw.get(
                    "banner_auto_hide_ms",
                    defaults.window.banner_auto_hide_ms,
                )
            ),
            control_bar_collapsed=bool(
                window_raw.get(
                    "control_bar_collapsed",
                    defaults.window.control_bar_collapsed,
                )
            ),
            normal=_merge_dataclass(
                type(defaults.window.normal),
                normal_raw,
            ),
            pip=_merge_dataclass(type(defaults.window.pip), pip_raw),
            fullscreen=_merge_dataclass(
                type(defaults.window.fullscreen),
                window_raw.get("fullscreen"),
            ),
        ),
        input=_merge_dataclass(InputConfig, input_raw),
        shortcuts=migrate_shortcuts(shortcuts_raw),
        watch_poll_interval_s=float(
            raw.get("watch_poll_interval_s", defaults.watch_poll_interval_s)
        ),
        watch_disconnect_debounce_s=float(
            raw.get(
                "watch_disconnect_debounce_s",
                defaults.watch_disconnect_debounce_s,
            )
        ),
        watch_autostart_enabled=bool(
            raw.get("watch_autostart_enabled", defaults.watch_autostart_enabled)
        ),
        updates=_merge_dataclass(type(defaults.updates), raw.get("updates")),
    )


def save_config(config: AppConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "adb": asdict(config.adb),
        "capture": asdict(config.capture),
        "window": {
            "last_mode": config.window.last_mode,
            "remember_geometry": config.window.remember_geometry,
            "aspect_ratio_locked": config.window.aspect_ratio_locked,
            "geometry_save_debounce_ms": config.window.geometry_save_debounce_ms,
            "chrome_auto_hide": config.window.chrome_auto_hide,
            "chrome_hide_delay_ms": config.window.chrome_hide_delay_ms,
            "banner_auto_hide_ms": config.window.banner_auto_hide_ms,
            "control_bar_collapsed": config.window.control_bar_collapsed,
            "normal": asdict(config.window.normal),
            "pip": asdict(config.window.pip),
            "fullscreen": asdict(config.window.fullscreen),
        },
        "input": asdict(config.input),
        "shortcuts": asdict(config.shortcuts),
        "watch_poll_interval_s": config.watch_poll_interval_s,
        "watch_disconnect_debounce_s": config.watch_disconnect_debounce_s,
        "watch_autostart_enabled": config.watch_autostart_enabled,
        "updates": asdict(config.updates),
    }
    SETTINGS_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def config_snapshot(config: AppConfig) -> AppConfig:
    from dataclasses import replace

    return replace(config)
