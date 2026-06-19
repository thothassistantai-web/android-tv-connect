"""Enumerate V4L2 video nodes and PipeWire/Pulse audio sources."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoDeviceOption:
    node: str
    description: str


@dataclass(frozen=True)
class AudioSourceOption:
    name: str
    description: str


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _video_device_label(entry: Path) -> str:
    node = f"/dev/{entry.name}"
    name = _read_text(entry / "name")
    if name:
        return f"{node} — {name}"
    device_link = entry / "device"
    if device_link.exists():
        try:
            parent = device_link.resolve()
        except OSError:
            parent = device_link
        product = _read_text(parent / "product")
        if product:
            return f"{node} — {product}"
    return node


def enumerate_v4l2_devices() -> list[VideoDeviceOption]:
    """Return capture-capable V4L2 nodes sorted by index."""
    base = Path("/sys/class/video4linux")
    if not base.is_dir():
        return []

    options: list[VideoDeviceOption] = []
    for entry in sorted(base.glob("video*"), key=lambda path: path.name):
        node = f"/dev/{entry.name}"
        if not Path(node).exists():
            continue
        options.append(VideoDeviceOption(node=node, description=_video_device_label(entry)))
    return options


def _parse_pactl_sources(stdout: str) -> list[AudioSourceOption]:
    options: list[AudioSourceOption] = []
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[1].strip()
        if not name or name.endswith(".monitor"):
            continue
        description = parts[-1].strip() if len(parts) >= 4 else name
        options.append(AudioSourceOption(name=name, description=description))
    return options


def _parse_pw_cli_nodes(stdout: str) -> list[AudioSourceOption]:
    options: list[AudioSourceOption] = []
    current_name = ""
    current_desc = ""
    for line in stdout.splitlines():
        name_match = re.search(r'node\.name\s*=\s*"([^"]+)"', line)
        if name_match:
            current_name = name_match.group(1)
        desc_match = re.search(r'node\.description\s*=\s*"([^"]+)"', line)
        if desc_match:
            current_desc = desc_match.group(1)
        if "MediaClass" in line and "Audio/Source" in line and current_name:
            if not current_name.endswith(".monitor"):
                options.append(
                    AudioSourceOption(
                        name=current_name,
                        description=current_desc or current_name,
                    )
                )
            current_name = ""
            current_desc = ""
    return options


def enumerate_audio_sources() -> list[AudioSourceOption]:
    """Return non-monitor audio input sources from pactl or PipeWire."""
    try:
        result = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        result = None

    if result is not None and result.returncode == 0 and result.stdout.strip():
        parsed = _parse_pactl_sources(result.stdout)
        if parsed:
            return parsed

    try:
        pw_result = subprocess.run(
            ["pw-cli", "ls", "Node"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if pw_result.returncode != 0:
        return []
    return _parse_pw_cli_nodes(pw_result.stdout)
