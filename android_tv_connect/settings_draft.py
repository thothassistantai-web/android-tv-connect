"""Draft vs saved settings helpers (pure, testable)."""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass

from .config import AppConfig, CaptureConfig


def _deep_copy_dataclass(obj):
    if not is_dataclass(obj):
        return obj
    return type(obj)(**{
        field.name: _deep_copy_dataclass(getattr(obj, field.name))
        for field in fields(obj)
    })


def config_snapshot(config: AppConfig) -> AppConfig:
    """Return an independent copy of *config* for revert-on-cancel."""
    return _deep_copy_dataclass(config)


def configs_equal(a: AppConfig, b: AppConfig) -> bool:
    """True when two configs would serialize identically."""
    return asdict(a) == asdict(b)


def configs_differ(a: AppConfig, b: AppConfig) -> bool:
    return not configs_equal(a, b)


def capture_stream_params(capture: CaptureConfig) -> tuple:
    """Fields that require a capture pipeline restart when changed."""
    return (
        capture.video_device,
        capture.audio_device,
        capture.width,
        capture.height,
        capture.framerate,
    )


def capture_stream_changed(before: CaptureConfig, after: CaptureConfig) -> bool:
    return capture_stream_params(before) != capture_stream_params(after)
