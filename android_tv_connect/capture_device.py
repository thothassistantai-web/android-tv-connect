"""Capture device discovery and USB presence helpers."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import CaptureConfig, default_capture_config

LOG = logging.getLogger(__name__)

AUTO_DEVICE = "auto"
_DISCOVERY_CACHE_TTL_S = 30.0
_USB_CACHE_TTL_S = 1.0

_CACHE_LOCK = threading.Lock()
_video_discovery_cache: dict[str, tuple[float, str | None]] = {}
_audio_discovery_cache: dict[str, tuple[float, str | None]] = {}
_usb_cache: dict[str, tuple[float, bool]] = {}
_warned_fallback: set[str] = set()

_CAPTURE_AUDIO_MARKERS = (
    "macrosilicon",
    "ms2109",
    "usb3.0_capture",
    "usb3.0 capture",
)
_DISABLED_AUDIO = frozenset({"disabled", "off", "none"})


@dataclass(frozen=True)
class CaptureDeviceStatus:
    usb_present: bool
    video_node: str | None
    configured_device: str
    effective_device: str | None

    @property
    def available(self) -> bool:
        return self.usb_present and self.effective_device is not None


def _usb_capture_present(vendor_product: str) -> bool:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _usb_cache.get(vendor_product)
        if cached and now - cached[0] < _USB_CACHE_TTL_S:
            return cached[1]

    vendor, product = vendor_product.lower().split(":")
    present = False
    sysfs_usb = Path("/sys/bus/usb/devices")
    if sysfs_usb.is_dir():
        for dev in sysfs_usb.iterdir():
            try:
                vid = (dev / "idVendor").read_text().strip().lower()
                pid = (dev / "idProduct").read_text().strip().lower()
            except OSError:
                continue
            if vid == vendor and pid == product:
                present = True
                break

    with _CACHE_LOCK:
        _usb_cache[vendor_product] = (now, present)
    return present


def is_capture_usb_present(config: CaptureConfig | None = None) -> bool:
    """Fast USB presence check — safe to call from the UI thread."""
    cfg = config or default_capture_config()
    return _usb_capture_present(cfg.usb_vendor_product)


def invalidate_capture_cache() -> None:
    with _CACHE_LOCK:
        _video_discovery_cache.clear()
        _audio_discovery_cache.clear()
        _usb_cache.clear()


def is_capture_audio_source(name: str) -> bool:
    """Return True when a PipeWire/Pulse source name looks like HDMI capture audio."""
    haystack = (name or "").lower()
    return any(marker in haystack for marker in _CAPTURE_AUDIO_MARKERS)


def _usb_parent_matches(device_path: Path, vendor: str, product: str) -> bool:
    try:
        resolved = device_path.resolve()
    except OSError:
        return False
    for parent in (resolved, *resolved.parents):
        vid_path = parent / "idVendor"
        pid_path = parent / "idProduct"
        if not vid_path.is_file() or not pid_path.is_file():
            continue
        try:
            vid = vid_path.read_text().strip().lower()
            pid = pid_path.read_text().strip().lower()
        except OSError:
            continue
        if vid == vendor and pid == product:
            return True
    return False


def _supports_mjpeg(device: str) -> bool:
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device, "--list-formats"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return False
    text = result.stdout.upper()
    return "MJPG" in text or "MOTION-JPEG" in text


def discover_video_device(vendor_product: str, *, use_cache: bool = True) -> str | None:
    """Return the V4L2 node for the MacroSilicon capture dongle, if any."""
    now = time.monotonic()
    if use_cache:
        with _CACHE_LOCK:
            cached = _video_discovery_cache.get(vendor_product)
            if cached and now - cached[0] < _DISCOVERY_CACHE_TTL_S:
                return cached[1]

    vendor, product = vendor_product.lower().split(":")
    base = Path("/sys/class/video4linux")
    if not base.is_dir():
        chosen = None
    else:
        candidates: list[tuple[int, str]] = []
        for entry in sorted(base.glob("video*")):
            node = f"/dev/{entry.name}"
            device_link = entry / "device"
            if not device_link.exists():
                continue
            if not _usb_parent_matches(device_link, vendor, product):
                continue
            if not _supports_mjpeg(node):
                continue
            try:
                index = int(entry.name.removeprefix("video"))
            except ValueError:
                index = 999
            candidates.append((index, node))

        chosen = None
        if candidates:
            candidates.sort(key=lambda item: item[0])
            chosen = candidates[0][1]
            LOG.debug("Discovered capture device %s for %s", chosen, vendor_product)

    with _CACHE_LOCK:
        _video_discovery_cache[vendor_product] = (now, chosen)
    return chosen


def _audio_source_available(name: str) -> bool:
    try:
        result = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return any(
        line.split("\t", 2)[1].strip() == name
        for line in result.stdout.splitlines()
        if "\t" in line
    )


def discover_audio_device(vendor_product: str, *, use_cache: bool = True) -> str | None:
    """Return the PipeWire/Pulse source for the MacroSilicon HDMI capture dongle."""
    cache_key = f"audio:{vendor_product}"
    now = time.monotonic()
    if use_cache:
        with _CACHE_LOCK:
            cached = _audio_discovery_cache.get(cache_key)
            if cached and now - cached[0] < _DISCOVERY_CACHE_TTL_S:
                return cached[1]

    from .media_enumeration import enumerate_audio_sources

    chosen = None
    for source in enumerate_audio_sources():
        if is_capture_audio_source(source.name) or is_capture_audio_source(
            source.description
        ):
            chosen = source.name
            break

    with _CACHE_LOCK:
        _audio_discovery_cache[cache_key] = (now, chosen)
    if chosen:
        LOG.debug("Discovered capture audio %s for %s", chosen, vendor_product)
    return chosen


def resolve_audio_device(config: CaptureConfig | None = None, *, use_cache: bool = True) -> str | None:
    """Resolve configured or auto-discovered HDMI capture audio source."""
    cfg = config or default_capture_config()
    configured = (cfg.audio_device or "").strip()

    if configured.lower() in _DISABLED_AUDIO:
        return None

    discovered = None
    if _usb_capture_present(cfg.usb_vendor_product):
        discovered = discover_audio_device(cfg.usb_vendor_product, use_cache=use_cache)

    if configured.lower() in ("", AUTO_DEVICE):
        return discovered

    if is_capture_audio_source(configured):
        return configured

    if discovered:
        key = f"audio:{configured}->{discovered}"
        if key not in _warned_fallback:
            _warned_fallback.add(key)
            LOG.warning(
                "Configured audio %s is not from HDMI capture; using %s",
                configured,
                discovered,
            )
        return discovered

    if _audio_source_available(configured):
        return configured

    if discovered:
        key = f"audio-missing:{configured}->{discovered}"
        if key not in _warned_fallback:
            _warned_fallback.add(key)
            LOG.warning(
                "Configured audio device %s unavailable; using %s",
                configured,
                discovered,
            )
        return discovered

    return configured


def resolve_video_device(config: CaptureConfig | None = None, *, use_cache: bool = True) -> str | None:
    """Resolve configured or auto-discovered V4L2 capture node."""
    cfg = config or default_capture_config()
    configured = (cfg.video_device or "").strip()

    if configured and configured.lower() != AUTO_DEVICE:
        if Path(configured).exists() and _supports_mjpeg(configured):
            return configured
        discovered = discover_video_device(cfg.usb_vendor_product, use_cache=use_cache)
        if discovered:
            key = f"{configured}->{discovered}"
            if key not in _warned_fallback:
                _warned_fallback.add(key)
                LOG.warning(
                    "Configured capture device %s unavailable; using %s",
                    configured,
                    discovered,
                )
            return discovered
        return None

    return discover_video_device(cfg.usb_vendor_product, use_cache=use_cache)


def capture_device_status(config: CaptureConfig | None = None, *, use_cache: bool = True) -> CaptureDeviceStatus:
    cfg = config or default_capture_config()
    usb_present = _usb_capture_present(cfg.usb_vendor_product)
    effective = resolve_video_device(cfg, use_cache=use_cache) if usb_present else None
    return CaptureDeviceStatus(
        usb_present=usb_present,
        video_node=effective,
        configured_device=cfg.video_device,
        effective_device=effective,
    )


def is_capture_device_available(config: CaptureConfig | None = None) -> bool:
    """Return True when the MS2109 USB device and a usable V4L2 node are present."""
    return capture_device_status(config, use_cache=True).available


def list_viable_audio_sources(sources=None):
    """Return all non-monitor PipeWire/Pulse inputs suitable for capture testing."""
    from .media_enumeration import enumerate_audio_sources

    if sources is not None:
        return list(sources)
    return enumerate_audio_sources()


_pipewiresrc_available: bool | None = None


def pipewiresrc_available() -> bool:
    """Return True when the native GStreamer pipewiresrc element is installed."""
    global _pipewiresrc_available
    if _pipewiresrc_available is not None:
        return _pipewiresrc_available
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        if not Gst.is_initialized():
            Gst.init(None)
        _pipewiresrc_available = Gst.ElementFactory.find("pipewiresrc") is not None
    except (ImportError, ValueError):
        _pipewiresrc_available = False
    return _pipewiresrc_available


def build_audio_source_segment(device: str) -> str:
    """Return the GStreamer launch prefix for HDMI capture audio input."""
    if pipewiresrc_available():
        return (
            f"pipewiresrc name=audiosrc target-object={device} do-timestamp=true ! "
            f"queue max-size-buffers=30 leaky=downstream ! "
        )
    return f"pulsesrc name=audiosrc device={device} ! "
