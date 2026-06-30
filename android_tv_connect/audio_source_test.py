"""Line-by-line audio source test helpers for Settings."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

from .media_enumeration import AudioSourceOption, enumerate_audio_sources

LOG = logging.getLogger(__name__)

AUDITION_PID_FILE = "/tmp/atv-audio-test-player.pid"


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


def build_pulsesrc_audition_pipeline(device: str) -> str:
    """Return a continuous pulsesrc audition pipeline (v1.1.10 live-capture path)."""
    name = (device or "").strip()
    return (
        f"pulsesrc device={name} ! "
        f"audioconvert ! audioresample ! "
        f"autoaudiosink sync=false"
    )


class AudioAuditionPlayer:
    """Play one capture input continuously until stopped."""

    def __init__(self) -> None:
        self._pipeline: Gst.Pipeline | None = None
        self._bus_watch_id: int | None = None
        self._device = ""

    @staticmethod
    def _ensure_gst_init() -> None:
        if not Gst.is_initialized():
            Gst.init(None)

    @property
    def device(self) -> str:
        return self._device

    def is_playing(self) -> bool:
        return self._pipeline is not None

    def _clear_bus_watch(self) -> None:
        if self._pipeline is None or self._bus_watch_id is None:
            self._bus_watch_id = None
            return
        bus = self._pipeline.get_bus()
        bus.remove_signal_watch()
        bus.disconnect(self._bus_watch_id)
        self._bus_watch_id = None

    def _on_bus_message(self, _bus: Gst.Bus, message: Gst.Message) -> bool:
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            LOG.warning("Audio audition error: %s (%s)", err, debug)
            self.stop()
        return True

    def start(self, device: str) -> bool:
        """Start continuous audition on *device*; replaces any active pipeline."""
        name = (device or "").strip()
        if not name:
            return False

        self.stop()
        self._ensure_gst_init()
        desc = build_pulsesrc_audition_pipeline(name)
        try:
            pipeline = Gst.parse_launch(desc)
        except Exception as exc:
            LOG.warning("Failed to build audition pipeline for %s: %s", name, exc)
            return False

        assert isinstance(pipeline, Gst.Pipeline)
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        self._bus_watch_id = bus.connect("message", self._on_bus_message)

        result = pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            self._clear_bus_watch()
            pipeline.set_state(Gst.State.NULL)
            return False

        self._pipeline = pipeline
        self._device = name
        LOG.info("Audio audition started on %s", name)
        return True

    def stop(self) -> None:
        """Tear down the audition pipeline."""
        if self._pipeline is None:
            self._device = ""
            return
        self._clear_bus_watch()
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None
        self._device = ""
