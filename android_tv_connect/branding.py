"""Application identity, version, and branding constants."""

from __future__ import annotations

from pathlib import Path

APP_ID = "com.androidtvconnect.App"
APP_NAME = "Android TV Connect"
APP_TAGLINE = "HDMI capture and ADB remote for Android TV sticks"
COPYRIGHT = "© 2026 Android TV Connect contributors"
WEBSITE = "https://github.com/thothassistantai-web/android-tv-connect"
ISSUE_TRACKER = WEBSITE + "/issues"
ICON_NAME = "android-tv-connect"

_PKG_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _PKG_ROOT.parent


def _read_version() -> str:
    for candidate in (
        _PROJECT_ROOT / "VERSION",
        _PKG_ROOT / "VERSION",
    ):
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
    return "0.0.0-dev"


VERSION = _read_version()
VERSION_DISPLAY = f"v{VERSION}"
