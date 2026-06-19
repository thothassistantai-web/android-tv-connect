"""Preset values and validation for the settings dialog."""

from __future__ import annotations

import re
from dataclasses import replace

from .adb_settings import normalize_adb_config, parse_wireless_port, wireless_host_warning
from .config import AdbConfig, CaptureConfig, ScrcpyConfig

RESOLUTION_PRESETS: tuple[tuple[str, int, int], ...] = (
    ("1920×1080", 1920, 1080),
    ("1280×720", 1280, 720),
    ("3840×2160", 3840, 2160),
    ("Native", 0, 0),
)

FRAMERATE_PRESETS: tuple[int, ...] = (30, 60, 24)

BIT_RATE_PRESETS: tuple[str, ...] = ("2M", "4M", "8M", "12M", "16M")

MAX_SIZE_PRESETS: tuple[tuple[str, int], ...] = (
    ("Native (0)", 0),
    ("720", 720),
    ("1080", 1080),
    ("1920", 1920),
    ("2560", 2560),
)

WIRELESS_PORT_PRESETS: tuple[int, ...] = (5555, 5037, 5556)

_BIT_RATE_RE = re.compile(r"^\d+[KMG]$", re.IGNORECASE)
_VIDEO_DEVICE_RE = re.compile(r"^/dev/video\d+$")


def resolution_label(width: int, height: int) -> str:
    for label, w, h in RESOLUTION_PRESETS:
        if w == width and h == height:
            return label
    if width > 0 and height > 0:
        return f"{width}×{height}"
    return "Native"


def resolution_index(width: int, height: int) -> int:
    for index, (_label, w, h) in enumerate(RESOLUTION_PRESETS):
        if w == width and h == height:
            return index
    return 0


def framerate_index(value: int) -> int:
    try:
        return FRAMERATE_PRESETS.index(value)
    except ValueError:
        return 0


def bit_rate_index(value: str) -> int:
    normalized = (value or ScrcpyConfig.bit_rate).strip().upper()
    for index, preset in enumerate(BIT_RATE_PRESETS):
        if preset.upper() == normalized:
            return index
    return BIT_RATE_PRESETS.index("8M")


def max_size_index(value: int) -> int:
    for index, (_label, size) in enumerate(MAX_SIZE_PRESETS):
        if size == value:
            return index
    return max_size_index(ScrcpyConfig.max_size)


def wireless_port_preset_index(port: int) -> int:
    try:
        return WIRELESS_PORT_PRESETS.index(port)
    except ValueError:
        return len(WIRELESS_PORT_PRESETS)


def parse_bit_rate(text: str) -> tuple[str | None, str | None]:
    stripped = (text or "").strip()
    if not stripped:
        return ScrcpyConfig.bit_rate, None
    upper = stripped.upper()
    for preset in BIT_RATE_PRESETS:
        if preset.upper() == upper:
            return preset, None
    if _BIT_RATE_RE.match(stripped):
        return stripped.upper(), None
    return None, "Bit rate must look like 8M or 4M"


def parse_max_size(value: int) -> tuple[int | None, str | None]:
    if value < 0:
        return None, "Max size cannot be negative"
    if value == 0:
        return 0, None
    if value > 3840:
        return None, "Max size cannot exceed 3840"
    return value, None


def normalize_video_device(value: str) -> str:
    stripped = (value or "").strip()
    if not stripped or stripped.lower() in {"", "auto", "*"}:
        return "auto"
    if _VIDEO_DEVICE_RE.match(stripped):
        return stripped
    return "auto"


def normalize_audio_device(value: str) -> str:
    stripped = (value or "").strip()
    if not stripped or stripped.lower() in {"", "auto", "*"}:
        return "auto"
    return stripped


def normalize_capture_config(capture: CaptureConfig) -> CaptureConfig:
    width = capture.width if capture.width >= 0 else 0
    height = capture.height if capture.height >= 0 else 0
    framerate = capture.framerate
    if framerate not in FRAMERATE_PRESETS:
        framerate = FRAMERATE_PRESETS[0]
    return replace(
        capture,
        video_device=normalize_video_device(capture.video_device),
        audio_device=normalize_audio_device(capture.audio_device),
        width=width,
        height=height,
        framerate=framerate,
    )


def normalize_scrcpy_config(scrcpy: ScrcpyConfig) -> ScrcpyConfig:
    bit_rate, _err = parse_bit_rate(scrcpy.bit_rate)
    max_size, _err = parse_max_size(scrcpy.max_size)
    return replace(
        scrcpy,
        bit_rate=bit_rate or ScrcpyConfig.bit_rate,
        max_size=max_size if max_size is not None else ScrcpyConfig.max_size,
        window_title=(scrcpy.window_title or ScrcpyConfig.window_title).strip()
        or ScrcpyConfig.window_title,
    )


def validate_adb_for_save(adb: AdbConfig) -> tuple[AdbConfig | None, str | None]:
    normalized = normalize_adb_config(adb)
    if normalized.wireless_host:
        warning = wireless_host_warning(normalized.wireless_host)
        if warning:
            return None, warning
    port, port_err = parse_wireless_port(str(normalized.wireless_port))
    if port_err or port is None:
        return None, port_err or "Invalid wireless port"
    if port != normalized.wireless_port:
        normalized = replace(normalized, wireless_port=port)
    return normalized, None
