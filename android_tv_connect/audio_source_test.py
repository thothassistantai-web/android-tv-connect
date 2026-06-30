"""Line-by-line audio source test helpers for Settings."""

from __future__ import annotations

from dataclasses import dataclass

from .media_enumeration import AudioSourceOption, enumerate_audio_sources


@dataclass(frozen=True)
class AudioTestSource:
    """One PipeWire/Pulse input to audition during settings testing."""

    name: str
    label: str


def list_viable_audio_sources(
    sources: list[AudioSourceOption] | None = None,
) -> list[AudioSourceOption]:
    """Return enumerated capture input sources (non-monitor)."""
    return list(sources if sources is not None else enumerate_audio_sources())


def build_audio_test_queue(
    sources: list[AudioSourceOption] | None = None,
    *,
    manual_name: str = "",
    include_auto_resolved: str | None = None,
    auto_label: str = "Auto (capture dongle)",
) -> list[AudioTestSource]:
    """Build ordered sources to test one-by-one."""
    options = list_viable_audio_sources(sources)
    queue: list[AudioTestSource] = [
        AudioTestSource(name=source.name, label=source.description or source.name)
        for source in options
    ]

    manual = (manual_name or "").strip()
    if manual and not any(item.name == manual for item in queue):
        queue.append(AudioTestSource(name=manual, label=manual))

    resolved = (include_auto_resolved or "").strip()
    if resolved and not any(item.name == resolved for item in queue):
        queue.insert(0, AudioTestSource(name=resolved, label=auto_label))

    return queue


def next_queue_index(queue: list[AudioTestSource], current_name: str) -> int | None:
    """Return the index after *current_name*, or None when exhausted."""
    if not queue:
        return None
    normalized = (current_name or "").strip()
    for index, item in enumerate(queue):
        if item.name == normalized:
            next_index = index + 1
            return next_index if next_index < len(queue) else None
    return 0 if queue else None
