"""Application configuration defaults for Android TV Connect."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .shortcuts import ShortcutsConfig

CONFIG_DIR = Path.home() / ".config" / "android-tv-connect"
WINDOW_CONFIG_PATH = CONFIG_DIR / "window.toml"


@dataclass(frozen=True)
class AdbConfig:
    wired_serial: str = "FUSA2541006925"
    wireless_host: str = "192.168.1.157"
    wireless_port: int = 5555


@dataclass(frozen=True)
class CaptureConfig:
    video_device: str = "auto"
    usb_vendor_product: str = "534d:2109"
    width: int = 1920
    height: int = 1080
    framerate: int = 30
    audio_device: str = (
        "alsa_input.usb-MACROSILICON_USB3.0_Capture-02.analog-stereo"
    )
    reconnect_interval_ms: int = 2000
    max_reconnect_backoff_ms: int = 10000


@dataclass(frozen=True)
class NormalWindowConfig:
    width: int = 1120
    height: int = 670
    x: int = -1
    y: int = -1
    maximized: bool = False
    monitor: int = 0


@dataclass(frozen=True)
class PipWindowConfig:
    width: int = 480
    height: int = 306
    x: int = 1480
    y: int = 820
    monitor: int = 0
    corner: str = "bottom-right"
    margin_px: int = 16
    opacity: float = 1.0
    soft_bar_visible: bool = True
    soft_bar_auto_hide: bool = True
    min_width: int = 320
    min_height: int = 180


@dataclass(frozen=True)
class FullscreenWindowConfig:
    monitor: int = 0


@dataclass(frozen=True)
class WindowConfig:
    last_mode: str = "normal"
    remember_geometry: bool = True
    aspect_ratio_locked: bool = True
    geometry_save_debounce_ms: int = 500
    chrome_auto_hide: bool = True
    chrome_hide_delay_ms: int = 2500
    banner_auto_hide_ms: int = 5000
    control_bar_collapsed: bool = True
    normal: NormalWindowConfig = field(default_factory=NormalWindowConfig)
    pip: PipWindowConfig = field(default_factory=PipWindowConfig)
    fullscreen: FullscreenWindowConfig = field(default_factory=FullscreenWindowConfig)


@dataclass(frozen=True)
class InputConfig:
    soft_buttons_work_unfocused: bool = True
    keyboard_requires_focus: bool = True
    click_to_control: bool = True
    release_on_unfocus: bool = True
    release_on_escape: bool = True
    scroll_threshold: float = 8.0
    default_pointer_mode: str = "nav"  # nav | mouse
    prefer_wired_adb: bool = True
    auto_keyboard_capture_on_focus: bool = False


@dataclass(frozen=True)
class AppConfig:
    adb: AdbConfig = field(default_factory=AdbConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    window: WindowConfig = field(default_factory=WindowConfig)
    input: InputConfig = field(default_factory=InputConfig)
    shortcuts: ShortcutsConfig = field(default_factory=ShortcutsConfig)
    watch_poll_interval_s: float = 2.0
    watch_disconnect_debounce_s: float = 3.0
    watch_autostart_enabled: bool = True


def default_config() -> AppConfig:
    return AppConfig()


def default_capture_config() -> CaptureConfig:
    return CaptureConfig()
