#!/usr/bin/env python3
"""Headless capture pipeline smoke test (no GTK window)."""

from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Gst

from android_tv_connect.capture import CapturePipeline
from android_tv_connect.settings_store import load_config


def main() -> int:
    Gst.init(None)
    cfg = load_config().capture
    states: list[str] = []
    errors: list[str] = []
    frames = 0

    class FakePicture:
        def set_paintable(self, _paintable) -> None:
            nonlocal frames
            frames += 1

    pipeline = CapturePipeline(
        cfg,
        on_state_change=states.append,
        on_error=errors.append,
    )
    pipeline.attach_video_widget(FakePicture())

    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(12, loop.quit)

    if not pipeline.start():
        print(f"start returned False, state={pipeline.state}")
    else:
        print(f"start returned True, state={pipeline.state}")

    loop.run()
    pipeline.stop()

    print(f"final_state={pipeline.state}")
    print(f"device={pipeline.effective_video_device}")
    print(f"states={states[-5:]}")
    print(f"frames={frames}")
    print(f"errors={errors[:3]}")
    ok = frames > 20 and not errors
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
