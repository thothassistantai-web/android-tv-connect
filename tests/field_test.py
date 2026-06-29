#!/usr/bin/env python3
"""Comprehensive field test for Android TV Connect (no device reboot)."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, ".")

from android_tv_connect.adb_client import AdbClient
from android_tv_connect.branding import APP_ID, APP_NAME, VERSION
from android_tv_connect.capture_device import (
    capture_device_status,
    discover_video_device,
    is_capture_device_available,
    is_capture_usb_present,
    resolve_video_device,
)
from android_tv_connect.settings_store import load_config
from android_tv_connect.singleton import is_ui_running


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "", duration_ms: float = 0.0) -> None:
        self.results.append(Result(name, ok, detail, duration_ms))

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)


def _run(cmd: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def test_version(report: Report) -> None:
    t0 = time.monotonic()
    proc = _run(["android-tv-connect", "--version"])
    ms = (time.monotonic() - t0) * 1000
    ok = proc.returncode == 0 and VERSION in proc.stdout
    report.add("CLI --version", ok, proc.stdout.strip() or proc.stderr.strip(), ms)


def test_config(report: Report) -> None:
    t0 = time.monotonic()
    try:
        cfg = load_config()
        ok = cfg.capture.video_device == "auto"
        detail = f"video={cfg.capture.video_device} adb={cfg.adb.wired_serial}"
    except Exception as exc:  # noqa: BLE001
        ok = False
        detail = str(exc)
    ms = (time.monotonic() - t0) * 1000
    report.add("Config load", ok, detail, ms)


def test_capture_usb(report: Report) -> None:
    cfg = load_config().capture
    t0 = time.monotonic()
    present = is_capture_usb_present(cfg)
    ms = (time.monotonic() - t0) * 1000
    report.add("Capture USB (fast)", present, f"534d:2109 present={present}", ms)


def test_capture_discovery(report: Report) -> None:
    cfg = load_config().capture
    t0 = time.monotonic()
    node = discover_video_device(cfg.usb_vendor_product, use_cache=False)
    ms = (time.monotonic() - t0) * 1000
    ok = node is not None and node.startswith("/dev/video")
    report.add("Capture discovery (full)", ok, f"node={node}", ms)


def test_capture_discovery_cached(report: Report) -> None:
    cfg = load_config().capture
    discover_video_device(cfg.usb_vendor_product, use_cache=True)
    times: list[float] = []
    for _ in range(20):
        t0 = time.monotonic()
        resolve_video_device(cfg, use_cache=True)
        times.append((time.monotonic() - t0) * 1000)
    avg = sum(times) / len(times)
    ok = avg < 5.0
    report.add(
        "Capture resolve cached (20x)",
        ok,
        f"avg={avg:.2f}ms max={max(times):.2f}ms",
        avg,
    )


def test_capture_available(report: Report) -> None:
    cfg = load_config().capture
    t0 = time.monotonic()
    ok = is_capture_device_available(cfg)
    status = capture_device_status(cfg)
    ms = (time.monotonic() - t0) * 1000
    report.add(
        "Capture available",
        ok,
        f"effective={status.effective_device}",
        ms,
    )


def test_gstreamer_pipeline(report: Report) -> None:
    cfg = load_config().capture
    node = resolve_video_device(cfg)
    if not node:
        report.add("GStreamer pipeline", False, "no video node")
        return

    busy = subprocess.run(
        ["fuser", node],
        capture_output=True,
        text=True,
        check=False,
    )
    if busy.returncode == 0:
        report.add(
            "GStreamer MJPEG pipeline",
            True,
            f"skipped ({node} in use by app)",
        )
        return

    desc = (
        f"v4l2src device={node} num-buffers=30 io-mode=2 ! "
        f"image/jpeg,width={cfg.width},height={cfg.height},framerate={cfg.framerate}/1 ! "
        f"jpegdec ! fakesink sync=false"
    )
    t0 = time.monotonic()
    proc = _run(["gst-launch-1.0", "-e"] + desc.split(), timeout=20.0)
    ms = (time.monotonic() - t0) * 1000
    ok = proc.returncode == 0
    combined = (proc.stderr or "") + (proc.stdout or "")
    jpeg_errors = combined.count("Failed to decode")
    tail = combined.strip().splitlines()[-1] if combined.strip() else ""
    detail = tail[:120]
    if jpeg_errors:
        detail += f" jpeg_errors={jpeg_errors}"
    report.add("GStreamer MJPEG pipeline", ok, detail, ms)


def test_adb_connect(report: Report) -> None:
    cfg = load_config()
    client = AdbClient(
        cfg.adb.wired_serial,
        cfg.adb.wireless_host,
        cfg.adb.wireless_port,
    )
    t0 = time.monotonic()
    ok = client.connect()
    ms = (time.monotonic() - t0) * 1000
    report.add("ADB connect", ok, f"connected={client.is_connected()}", ms)
    if ok:
        client.disconnect()


def test_adb_is_connected_fast(report: Report) -> None:
    cfg = load_config()
    client = AdbClient(
        cfg.adb.wired_serial,
        cfg.adb.wireless_host,
        cfg.adb.wireless_port,
    )
    if not client.connect():
        report.add("ADB is_connected cache speed", False, "connect failed")
        return

    times: list[float] = []
    for _ in range(50):
        t0 = time.monotonic()
        client.is_connected()
        times.append((time.monotonic() - t0) * 1000)

    avg = sum(times) / len(times)
    ok = avg < 0.1
    report.add(
        "ADB is_connected cache (50x)",
        ok,
        f"avg={avg:.4f}ms max={max(times):.4f}ms",
        avg,
    )
    client.disconnect()


def test_adb_keyevent(report: Report) -> None:
    cfg = load_config()
    client = AdbClient(
        cfg.adb.wired_serial,
        cfg.adb.wireless_host,
        cfg.adb.wireless_port,
    )
    if not client.connect():
        report.add("ADB keyevent (DPAD_UP)", False, "connect failed")
        return
    t0 = time.monotonic()
    ok = client.keyevent(19)
    ms = (time.monotonic() - t0) * 1000
    report.add("ADB keyevent (DPAD_UP)", ok, "sent keycode 19", ms)
    client.disconnect()


def test_adb_health_poll(report: Report) -> None:
    cfg = load_config()
    changes: list[bool] = []

    def on_change(connected: bool) -> None:
        changes.append(connected)

    client = AdbClient(
        cfg.adb.wired_serial,
        cfg.adb.wireless_host,
        cfg.adb.wireless_port,
        on_connection_change=on_change,
    )
    if not client.connect():
        report.add("ADB health poll (6s)", False, "connect failed")
        return

    t0 = time.monotonic()
    time.sleep(6.0)
    ms = (time.monotonic() - t0) * 1000
    ok = client.is_connected()
    report.add(
        "ADB health poll (6s)",
        ok,
        f"still connected, callbacks={len(changes)}",
        ms,
    )
    client.disconnect()


def test_watch_readiness(report: Report) -> None:
    from android_tv_connect.__main__ import _both_ready

    cfg = load_config()
    t0 = time.monotonic()
    ready = _both_ready(cfg)
    ms = (time.monotonic() - t0) * 1000
    report.add("Watch _both_ready", ready, f"ready={ready}", ms)


def test_ui_running(report: Report) -> None:
    t0 = time.monotonic()
    running = is_ui_running()
    ms = (time.monotonic() - t0) * 1000
    report.add("UI running check", True, f"running={running}", ms)


def test_dbus_name(report: Report) -> None:
    t0 = time.monotonic()
    proc = _run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.freedesktop.DBus",
            "--object-path",
            "/org/freedesktop/DBus",
            "--method",
            "org.freedesktop.DBus.NameHasOwner",
            APP_ID,
        ],
        timeout=3.0,
    )
    ms = (time.monotonic() - t0) * 1000
    owned = "(true)" in proc.stdout
    report.add("D-Bus app registration", True, f"{APP_ID} owned={owned}", ms)


def test_audio_source(report: Report) -> None:
    from android_tv_connect.capture_device import resolve_audio_device

    cfg = load_config().capture
    resolved = resolve_audio_device(cfg) or cfg.audio_device
    t0 = time.monotonic()
    proc = _run(["pactl", "list", "sources", "short"])
    ms = (time.monotonic() - t0) * 1000
    ok = resolved in proc.stdout
    report.add("PipeWire audio source", ok, resolved, ms)


def run_all(skip_gstreamer: bool = False) -> Report:
    report = Report()
    print(f"=== {APP_NAME} field test ===\n")
    test_version(report)
    test_config(report)
    test_capture_usb(report)
    test_capture_discovery(report)
    test_capture_discovery_cached(report)
    test_capture_available(report)
    if not skip_gstreamer:
        test_gstreamer_pipeline(report)
    test_adb_connect(report)
    test_adb_is_connected_fast(report)
    test_adb_keyevent(report)
    test_adb_health_poll(report)
    test_watch_readiness(report)
    test_ui_running(report)
    test_dbus_name(report)
    test_audio_source(report)
    return report


def print_report(report: Report) -> int:
    width = max(len(r.name) for r in report.results) + 2
    for r in report.results:
        mark = "PASS" if r.ok else "FAIL"
        timing = f" ({r.duration_ms:.1f}ms)" if r.duration_ms else ""
        print(f"{mark:4}  {r.name:<{width}}{timing}")
        if r.detail:
            print(f"       {r.detail}")

    print(f"\n{report.passed}/{len(report.results)} passed")
    return 0 if report.failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Field test Android TV Connect")
    parser.add_argument("--skip-gstreamer", action="store_true")
    args = parser.parse_args()
    report = run_all(skip_gstreamer=args.skip_gstreamer)
    return print_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
