"""Android TV Connect — HDMI capture and ADB control for Android TV sticks."""

from .branding import APP_NAME, VERSION

__version__ = VERSION
__all__ = ["APP_NAME", "VERSION", "__version__"]

from .adb_client import AdbClient
from .capture import CapturePipeline
from .capture_device import is_capture_device_available
from .config import AppConfig, CaptureConfig, default_config, default_capture_config
from .window import AndroidTvApp, MainWindow

__all__ += [
    "AdbClient",
    "AndroidTvApp",
    "AppConfig",
    "CaptureConfig",
    "CapturePipeline",
    "MainWindow",
    "default_capture_config",
    "default_config",
    "is_capture_device_available",
]
