"""GStreamer capture for MacroSilicon MS2109 HDMI dongles."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gst

from .capture_device import (
    build_audio_sink_segment,
    build_audio_source_segment,
    capture_device_status,
    ensure_capture_playback_unmuted,
    invalidate_capture_cache,
    is_capture_usb_present,
    resolve_audio_device,
    resolve_video_device,
)
from .config import CaptureConfig, default_capture_config

LOG = logging.getLogger(__name__)

StateCallback = Callable[[str], None]
ErrorCallback = Callable[[str], None]
UsbCallback = Callable[[], None]

DEVICE_WATCH_MS = 2000
FRAME_STALL_S = 8.0
RECONNECT_STUCK_S = 45.0
AUDIO_STATE_TIMEOUT_MS = 2000


class CapturePipeline:
    """Low-latency MJPEG video + PipeWire audio capture with auto-reconnect."""

    def __init__(
        self,
        config: CaptureConfig | None = None,
        on_state_change: StateCallback | None = None,
        on_error: ErrorCallback | None = None,
        on_usb_unplugged: UsbCallback | None = None,
        on_usb_reconnected: UsbCallback | None = None,
    ) -> None:
        self.config = config or default_capture_config()
        self._on_state_change = on_state_change
        self._on_error = on_error
        self._on_usb_unplugged = on_usb_unplugged
        self._on_usb_reconnected = on_usb_reconnected

        self._video_pipeline: Gst.Pipeline | None = None
        self._audio_pipeline: Gst.Pipeline | None = None
        self._appsink: Gst.Element | None = None
        self._picture = None
        self._sample_handler_id: int | None = None

        self._video_bus_watch_id: int | None = None
        self._audio_bus_watch_id: int | None = None
        self._reconnect_source_id: int | None = None
        self._device_watch_source_id: int | None = None
        self._reconnect_backoff_ms = self.config.reconnect_interval_ms

        self._running = False
        self._state = "stopped"
        self._effective_video_device: str | None = None
        self._effective_audio_device: str | None = None
        self._usb_present_last = True
        self._unplug_notified = False
        self._last_frame_at = 0.0
        self._reconnecting_since = 0.0
        self._user_paused = False
        self._config_lock = threading.Lock()

    @staticmethod
    def _ensure_gst_init() -> None:
        if not Gst.is_initialized():
            Gst.init(None)

    def _set_state(self, state: str) -> None:
        if self._state == state:
            return
        if state in ("reconnecting", "waiting", "disconnected"):
            if self._reconnecting_since <= 0:
                self._reconnecting_since = time.monotonic()
        elif state == "playing":
            self._reconnecting_since = 0.0
        self._state = state
        LOG.debug("capture state -> %s", state)
        if self._on_state_change:
            GLib.idle_add(self._deliver_state_change, state)

    def _deliver_state_change(self, state: str) -> bool:
        if self._on_state_change:
            self._on_state_change(state)
        return False

    def _emit_error(self, message: str) -> None:
        LOG.error("%s", message)
        if self._on_error:
            GLib.idle_add(self._deliver_error, message)

    def _deliver_error(self, message: str) -> bool:
        if self._on_error:
            self._on_error(message)
        return False

    def _video_pipeline_desc(self) -> str:
        cfg = self.config
        device = self._effective_video_device or cfg.video_device
        size_caps = ""
        if cfg.width > 0 and cfg.height > 0:
            size_caps = f"width={cfg.width},height={cfg.height},"
        return (
            f"v4l2src name=src device={device} io-mode=2 ! "
            f"image/jpeg,{size_caps}"
            f"framerate={cfg.framerate}/1 ! "
            f"jpegdec ! videoconvert ! video/x-raw,format=RGB ! "
            f"appsink name=video_sink emit-signals=true max-buffers=1 "
            f"drop=true sync=false"
        )

    def _audio_pipeline_desc(self) -> str:
        device = (self._effective_audio_device or "").strip()
        if not device:
            return ""
        return (
            f"{build_audio_source_segment(device)}"
            f"{build_audio_sink_segment()}"
        )

    def attach_video_widget(self, picture) -> None:
        self._picture = picture

    def _on_new_sample(self, sink: Gst.Element) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR

        buffer = sample.get_buffer()
        caps = sample.get_caps()
        if buffer is None or caps is None:
            return Gst.FlowReturn.ERROR

        structure = caps.get_structure(0)
        width = structure.get_value("width")
        height = structure.get_value("height")
        ok, mapinfo = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR

        try:
            stride = width * 3
            data = bytes(mapinfo.data[: stride * height])
        finally:
            buffer.unmap(mapinfo)

        self._last_frame_at = time.monotonic()
        GLib.idle_add(self._present_frame, width, height, data)
        return Gst.FlowReturn.OK

    def _present_frame(self, width: int, height: int, data: bytes) -> bool:
        if self._picture is None or not hasattr(self._picture, "set_paintable"):
            return False
        stride = width * 3
        texture = Gdk.MemoryTexture.new(
            width,
            height,
            Gdk.MemoryFormat.R8G8B8,
            GLib.Bytes.new(data),
            stride,
        )
        self._picture.set_paintable(texture)
        return False

    def _on_bus_message(
        self,
        _bus: Gst.Bus,
        message: Gst.Message,
        pipeline_kind: str,
    ) -> bool:
        msg_type = message.type
        if msg_type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self._emit_error(f"{pipeline_kind} pipeline error: {err} ({debug})")
            if pipeline_kind == "video":
                self._handle_video_failure()
            return True

        if msg_type == Gst.MessageType.EOS:
            LOG.warning("%s pipeline ended (EOS)", pipeline_kind)
            if pipeline_kind == "video":
                self._handle_video_failure()
            return True

        if msg_type == Gst.MessageType.STATE_CHANGED and pipeline_kind == "video":
            if message.src is not self._video_pipeline:
                return True
            _old, new, _pending = message.parse_state_changed()
            if new == Gst.State.PLAYING:
                self._set_state("playing")
            elif new == Gst.State.PAUSED:
                self._set_state("paused")
        return True

    def _add_bus_watch(self, pipeline: Gst.Pipeline, pipeline_kind: str) -> int:
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        return bus.connect("message", self._on_bus_message, pipeline_kind)

    def _resolve_device(self) -> str | None:
        self._effective_video_device = resolve_video_device(self.config)
        return self._effective_video_device

    def _create_video_pipeline(self) -> bool:
        if not self._resolve_device():
            return False
        try:
            self._video_pipeline = Gst.parse_launch(self._video_pipeline_desc())
        except GLib.Error as exc:
            self._emit_error(f"Failed to build video pipeline: {exc}")
            self._video_pipeline = None
            return False

        assert self._video_pipeline is not None
        self._appsink = self._video_pipeline.get_by_name("video_sink")
        if self._appsink is None:
            self._emit_error("Video pipeline missing video_sink appsink")
            return False

        self._sample_handler_id = self._appsink.connect("new-sample", self._on_new_sample)
        self._video_bus_watch_id = self._add_bus_watch(self._video_pipeline, "video")
        return True

    def _create_audio_pipeline(self) -> bool:
        self._effective_audio_device = resolve_audio_device(self.config)
        if not self._effective_audio_device:
            return False
        try:
            self._audio_pipeline = Gst.parse_launch(self._audio_pipeline_desc())
        except GLib.Error as exc:
            self._emit_error(f"Failed to build audio pipeline: {exc}")
            self._audio_pipeline = None
            return False

        assert self._audio_pipeline is not None
        self._audio_bus_watch_id = self._add_bus_watch(self._audio_pipeline, "audio")
        return True

    def _set_pipeline_state(
        self,
        pipeline: Gst.Pipeline,
        state: Gst.State,
        *,
        timeout_ms: int = 100,
    ) -> bool:
        result = pipeline.set_state(state)
        if result == Gst.StateChangeReturn.FAILURE:
            return False
        if result == Gst.StateChangeReturn.ASYNC:
            _change, current, pending = pipeline.get_state(timeout_ms * Gst.MSECOND)
            return current == state or pending == state
        return True

    def _start_pipelines(self) -> bool:
        status = capture_device_status(self.config)
        if not status.usb_present:
            self._set_state("disconnected")
            return False
        if not status.effective_device:
            self._set_state("waiting")
            return False

        invalidate_capture_cache()
        self._effective_video_device = status.effective_device

        if not self._create_video_pipeline():
            return False

        if not self._set_pipeline_state(self._video_pipeline, Gst.State.PLAYING):
            self._emit_error("Failed to start video pipeline")
            self._teardown_pipelines()
            return False

        if self._create_audio_pipeline():
            if not self._set_pipeline_state(
                self._audio_pipeline,
                Gst.State.PLAYING,
                timeout_ms=AUDIO_STATE_TIMEOUT_MS,
            ):
                LOG.warning(
                    "Audio pipeline failed to start for %s; continuing video-only",
                    self._effective_audio_device,
                )
                self._teardown_audio_pipeline()
            else:
                sink = self._audio_pipeline.get_by_name("audiosink")
                if sink is not None:
                    sink.set_property("mute", False)
                    sink.set_property("volume", 1.0)
                ensure_capture_playback_unmuted()
                LOG.info("Audio capture started on %s", self._effective_audio_device)
        else:
            LOG.warning("Audio pipeline unavailable; continuing video-only")

        self._reconnect_backoff_ms = self.config.reconnect_interval_ms
        self._last_frame_at = time.monotonic()
        self._set_state("playing")
        return True

    def _clear_bus_watch(self, pipeline: Gst.Pipeline | None, watch_id: int | None) -> None:
        if pipeline is None or watch_id is None:
            return
        bus = pipeline.get_bus()
        bus.remove_signal_watch()
        bus.disconnect(watch_id)

    def _teardown_audio_pipeline(self) -> None:
        if self._audio_pipeline is not None:
            self._audio_pipeline.set_state(Gst.State.NULL)
        self._clear_bus_watch(self._audio_pipeline, self._audio_bus_watch_id)
        self._audio_bus_watch_id = None
        self._audio_pipeline = None

    def _teardown_video_pipeline(self) -> None:
        if self._appsink is not None and self._sample_handler_id is not None:
            self._appsink.disconnect(self._sample_handler_id)
            self._sample_handler_id = None

        if self._video_pipeline is not None:
            self._video_pipeline.set_state(Gst.State.NULL)
        self._clear_bus_watch(self._video_pipeline, self._video_bus_watch_id)
        self._video_bus_watch_id = None
        self._video_pipeline = None
        self._appsink = None

    def _teardown_pipelines(self) -> None:
        self._teardown_audio_pipeline()
        self._teardown_video_pipeline()

    def _cancel_reconnect(self) -> None:
        if self._reconnect_source_id is not None:
            GLib.source_remove(self._reconnect_source_id)
            self._reconnect_source_id = None

    def _handle_video_failure(self) -> None:
        if not self._running or self._user_paused:
            return
        threading.Thread(
            target=self._handle_video_failure_worker,
            daemon=True,
            name="capture-failure",
        ).start()

    def _handle_video_failure_worker(self) -> None:
        if not self._running or self._user_paused:
            return
        with self._config_lock:
            self._teardown_pipelines()
            status = capture_device_status(self.config)
        if not status.usb_present:
            self._set_state("disconnected")
        else:
            self._set_state("reconnecting")
        self._schedule_reconnect(immediate=not status.usb_present)

    def _schedule_reconnect(self, *, immediate: bool = False) -> None:
        if not self._running:
            return
        GLib.idle_add(self._schedule_reconnect_idle, immediate)

    def _schedule_reconnect_idle(self, immediate: bool) -> bool:
        if not self._running:
            return False
        self._cancel_reconnect()
        delay = 0 if immediate else self._reconnect_backoff_ms
        LOG.info("Scheduling capture reconnect in %d ms", delay)
        self._reconnect_source_id = GLib.timeout_add(max(0, delay), self._attempt_reconnect)
        return False

    def _attempt_reconnect(self) -> bool:
        self._reconnect_source_id = None
        if not self._running or self._user_paused:
            return False
        threading.Thread(
            target=self._attempt_reconnect_worker,
            daemon=True,
            name="capture-reconnect",
        ).start()
        return False

    def _attempt_reconnect_worker(self) -> None:
        if not self._running or self._user_paused:
            return

        with self._config_lock:
            if self._reconnecting_since > 0:
                stuck_for = time.monotonic() - self._reconnecting_since
                if stuck_for >= RECONNECT_STUCK_S:
                    LOG.warning(
                        "Capture reconnect stuck %.0fs — forcing pipeline reset",
                        stuck_for,
                    )
                    self._teardown_pipelines()
                    self._reconnect_backoff_ms = self.config.reconnect_interval_ms

            started = self._start_pipelines()

        if started:
            return

        status = capture_device_status(self.config)
        if not status.usb_present:
            self._set_state("disconnected")
        elif not status.effective_device:
            self._set_state("waiting")
        else:
            self._set_state("reconnecting")

        self._reconnect_backoff_ms = min(
            self._reconnect_backoff_ms * 2,
            self.config.max_reconnect_backoff_ms,
        )
        self._schedule_reconnect()

    def _start_device_watch(self) -> None:
        if self._device_watch_source_id is not None:
            return
        status = capture_device_status(self.config)
        self._usb_present_last = status.usb_present
        self._device_watch_source_id = GLib.timeout_add(
            DEVICE_WATCH_MS,
            self._on_device_watch,
        )

    def _stop_device_watch(self) -> None:
        if self._device_watch_source_id is not None:
            GLib.source_remove(self._device_watch_source_id)
            self._device_watch_source_id = None

    def _on_device_watch(self) -> bool:
        if not self._running:
            return False

        usb_present = is_capture_usb_present(self.config)

        if usb_present and not self._usb_present_last:
            LOG.info("Capture USB device reconnected")
            invalidate_capture_cache()
            self._unplug_notified = False
            self._reconnect_backoff_ms = self.config.reconnect_interval_ms
            if self._on_usb_reconnected:
                self._on_usb_reconnected()
            if not self._user_paused and self._state != "playing":
                self._schedule_reconnect(immediate=True)

        if not usb_present and self._usb_present_last:
            LOG.warning("Capture USB device unplugged")
            invalidate_capture_cache()
            threading.Thread(
                target=self._teardown_pipelines,
                daemon=True,
                name="capture-unplug-teardown",
            ).start()
            self._set_state("disconnected")
            if not self._unplug_notified and self._on_usb_unplugged:
                self._unplug_notified = True
                self._on_usb_unplugged()
            if not self._user_paused:
                self._schedule_reconnect(immediate=True)

        self._usb_present_last = usb_present

        if (
            not self._user_paused
            and self._state == "playing"
            and self._last_frame_at > 0
            and time.monotonic() - self._last_frame_at > FRAME_STALL_S
        ):
            LOG.warning("Capture frame stall detected")
            invalidate_capture_cache()
            self._handle_video_failure()

        return True

    def pause_by_user(self) -> None:
        """Stop video/audio pipelines until the user resumes."""
        if not self._running or self._state != "playing":
            return
        self._user_paused = True
        self._cancel_reconnect()
        self._teardown_pipelines()
        self._set_state("paused")

    def resume_by_user(self) -> None:
        """Resume capture after a user pause or manual reconnect."""
        self._user_paused = False
        if not self._running:
            self.start()
            return
        self._cancel_reconnect()
        invalidate_capture_cache()
        self._set_state("reconnecting")
        self._schedule_reconnect(immediate=True)

    @property
    def user_paused(self) -> bool:
        return self._user_paused

    def start(self) -> bool:
        self._ensure_gst_init()
        if self._running:
            return self._state == "playing"

        self._running = True
        self._user_paused = False
        self._cancel_reconnect()
        self._set_state("starting")
        self._start_device_watch()

        if self._start_pipelines():
            return True

        status = capture_device_status(self.config)
        if not status.usb_present:
            self._set_state("disconnected")
        else:
            self._set_state("reconnecting")
        self._schedule_reconnect()
        return False

    def stop(self) -> None:
        self._running = False
        self._cancel_reconnect()
        self._stop_device_watch()
        self._teardown_pipelines()
        self._reconnect_backoff_ms = self.config.reconnect_interval_ms
        self._reconnecting_since = 0.0
        self._set_state("stopped")

    def is_running(self) -> bool:
        return self._running

    @property
    def state(self) -> str:
        return self._state

    @property
    def effective_video_device(self) -> str | None:
        return self._effective_video_device

    @property
    def effective_audio_device(self) -> str | None:
        return self._effective_audio_device

    def get_audio_volume(self) -> float | None:
        """Return capture playback volume (0.0–1.0) when audio pipeline is active."""
        pipeline = self._audio_pipeline
        if pipeline is None:
            return None
        sink = pipeline.get_by_name("audiosink")
        if sink is None:
            return None
        return float(sink.get_property("volume"))

    def set_audio_volume(self, volume: float) -> bool:
        """Set capture playback volume (0.0–1.0) on the active audio sink."""
        pipeline = self._audio_pipeline
        if pipeline is None:
            return False
        sink = pipeline.get_by_name("audiosink")
        if sink is None:
            return False
        clamped = max(0.0, min(1.0, volume))
        sink.set_property("volume", clamped)
        return True

    @staticmethod
    def _stream_params(config: CaptureConfig) -> tuple:
        return (
            config.video_device,
            config.audio_device,
            config.width,
            config.height,
            config.framerate,
        )

    def with_config(self, config: CaptureConfig) -> None:
        """Hot-swap capture settings; restart pipelines when stream params change."""
        with self._config_lock:
            params_changed = self._stream_params(self.config) != self._stream_params(config)
            self.config = config
            if not self._running:
                return
            self._reconnect_backoff_ms = self.config.reconnect_interval_ms
            if params_changed and self._state == "playing":
                self._teardown_pipelines()
                self._set_state("reconnecting")
                self._schedule_reconnect(immediate=True)
            elif self._state != "playing":
                self._schedule_reconnect(immediate=True)
