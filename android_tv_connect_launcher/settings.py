"""Read launcher update preferences from the main app config file."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .constants import DEFAULT_UPDATE_MANIFEST_URL

CONFIG_DIR = Path.home() / ".config" / "android-tv-connect"
SETTINGS_PATH = CONFIG_DIR / "config.json"


@dataclass(frozen=True)
class LauncherSettings:
    auto_check_updates: bool = True
    update_manifest_url: str = DEFAULT_UPDATE_MANIFEST_URL
    dismissed_update_version_code: int = 0


def load_launcher_settings() -> LauncherSettings:
    if not SETTINGS_PATH.is_file():
        return LauncherSettings()

    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return LauncherSettings()

    updates = raw.get("updates", {})
    override = str(updates.get("manifest_url_override", "")).strip()
    manifest_url = override or DEFAULT_UPDATE_MANIFEST_URL
    return LauncherSettings(
        auto_check_updates=bool(updates.get("auto_check_on_launch", True)),
        update_manifest_url=manifest_url,
        dismissed_update_version_code=int(updates.get("dismissed_version_code", 0)),
    )
