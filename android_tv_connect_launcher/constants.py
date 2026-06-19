"""Launcher constants — change rarely (updater bugs only)."""

from __future__ import annotations

from pathlib import Path

LAUNCHER_VERSION = "1.0.0"

MANIFEST_ASSET_NAME = "update-manifest.json"

DEFAULT_UPDATE_MANIFEST_URL = (
    "https://api.github.com/repos/thothassistantai-web/android-tv-connect/releases/latest"
)

DEFAULT_GITHUB_RAW_MANIFEST_URL = (
    "https://github.com/thothassistantai-web/android-tv-connect/releases/latest/download/"
    f"{MANIFEST_ASSET_NAME}"
)

DEFAULT_DATA_ROOT = Path.home() / ".local" / "share" / "android-tv-connect"
USER_AGENT = f"AndroidTVConnectLauncher/{LAUNCHER_VERSION}"
