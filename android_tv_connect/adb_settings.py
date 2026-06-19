"""Normalize and validate ADB connection settings."""

from __future__ import annotations

import re

from .config import AdbConfig

_AUTO_SERIAL_VALUES = frozenset({"", "auto", "*"})
_HOST_RE = re.compile(r"^[a-zA-Z0-9.\-:]+$")


def normalize_wired_serial(value: str) -> str:
    """Empty string means auto-discover the first available USB ADB device."""
    stripped = value.strip()
    if stripped.lower() in _AUTO_SERIAL_VALUES:
        return ""
    return stripped


def normalize_wireless_host(value: str, *, default: str) -> str:
    stripped = value.strip()
    return stripped or default


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

    return replace(
        adb,
        wired_serial=normalize_wired_serial(adb.wired_serial),
        wireless_host=normalize_wireless_host(
            adb.wireless_host, default=AdbConfig.wireless_host
        ),
    )


def wireless_host_warning(value: str) -> str | None:
    """Return a non-blocking warning for unusual host values."""
    stripped = value.strip()
    if not stripped:
        return None
    if _HOST_RE.match(stripped):
        return None
    return "Host contains unusual characters; ADB may fail to connect"
