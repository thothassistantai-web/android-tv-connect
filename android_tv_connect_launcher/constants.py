"""Launcher constants — change rarely (updater bugs only)."""

from __future__ import annotations

from pathlib import Path

LAUNCHER_VERSION = "1.0.0"

DEFAULT_UPDATE_MANIFEST_URL = (
    "https://api.github.com/repos/thothassistantai-web/android-tv-connect/releases/latest"
)

DATA_ROOT = Path.home() / ".local" / "share" / "android-tv-connect"
LAUNCHER_DIR = DATA_ROOT / "launcher"
VERSIONS_DIR = DATA_ROOT / "versions"
CURRENT_LINK = DATA_ROOT / "current"
INSTALLED_META = DATA_ROOT / "installed.json"
MANIFEST_ASSET_NAME = "update-manifest.json"
USER_AGENT = f"AndroidTVConnectLauncher/{LAUNCHER_VERSION}"
