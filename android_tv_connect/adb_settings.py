"""Normalize and validate ADB connection settings."""

from __future__ import annotations

import re

from .config import AdbConfig
from .connection_ui import LEGACY_WIRED_SERIAL, LEGACY_WIRELESS_HOST

_AUTO_VALUES = frozenset({"", "auto", "*"})
_HOST_RE = re.compile(r"^[a-zA-Z0-9.\-:]+$")


def normalize_wired_serial(value: str) -> str:
    """Empty string means auto-discover the first available USB ADB device."""
    stripped = value.strip()
    if stripped.lower() in _AUTO_VALUES:
        return ""
    return stripped


def normalize_wireless_host(value: str) -> str:
    """Empty string means auto-discover the first available wireless ADB device."""
    stripped = value.strip()
    if stripped.lower() in _AUTO_VALUES:
        return ""
    return stripped


def migrate_legacy_adb_defaults(adb: AdbConfig) -> AdbConfig:
    """Convert shipped dev-host defaults to neutral auto mode without touching custom values."""
    from dataclasses import replace

    changes: dict[str, str] = {}
    if adb.wired_serial.strip() == LEGACY_WIRED_SERIAL:
        changes["wired_serial"] = ""
    if adb.wireless_host.strip() == LEGACY_WIRELESS_HOST:
        changes["wireless_host"] = ""
    if changes:
        adb = replace(adb, **changes)
    return adb


def wireless_host_is_auto(value: str) -> bool:
    return not value.strip() or value.strip().lower() in _AUTO_VALUES


def parse_wireless_port(text: str, *, default: int = 5555) -> tuple[int | None, str | None]:
    stripped = text.strip()
    if not stripped:
        return default, None
    try:
        port = int(stripped)
    except ValueError:
        return None, "Wireless port must be a number"
    if not 1 <= port <= 65535:
        return None, "Wireless port must be between 1 and 65535"
    return port, None


def normalize_adb_config(adb: AdbConfig) -> AdbConfig:
    from dataclasses import replace

    adb = migrate_legacy_adb_defaults(adb)
    return replace(
        adb,
        wired_serial=normalize_wired_serial(adb.wired_serial),
        wireless_host=normalize_wireless_host(adb.wireless_host),
    )


def wireless_host_warning(value: str) -> str | None:
    """Return a non-blocking warning for unusual host values."""
    stripped = value.strip()
    if not stripped:
        return None
    if _HOST_RE.match(stripped):
        return None
    return "Host contains unusual characters; ADB may fail to connect"
