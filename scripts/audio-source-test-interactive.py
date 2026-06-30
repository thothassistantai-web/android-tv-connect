#!/usr/bin/env python3
"""Play one HDMI capture audio source continuously until stopped interactively."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from android_tv_connect.audio_source_test import (
    AUDITION_PID_FILE,
    build_audio_test_queue,
    build_pulsesrc_audition_pipeline,
)
from android_tv_connect.capture_device import resolve_audio_device
from android_tv_connect.config import default_capture_config
from android_tv_connect.media_enumeration import enumerate_audio_sources


def _read_pid() -> int | None:
    try:
        text = Path(AUDITION_PID_FILE).read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _stop_existing() -> None:
    pid = _read_pid()
    if pid is None:
        return
    if _is_running(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                if not _is_running(pid):
                    break
                time.sleep(0.05)
            if _is_running(pid):
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        Path(AUDITION_PID_FILE).unlink(missing_ok=True)
    except OSError:
        pass


def _resolve_source_name(
    *,
    source: str,
    index: int | None,
) -> tuple[str, str]:
    cfg = default_capture_config()
    if source.strip():
        return source.strip(), source.strip()

    queue = build_audio_test_queue(
        enumerate_audio_sources(),
        include_auto_resolved=resolve_audio_device(cfg),
        auto_label="Auto (capture dongle)",
    )
    if not queue:
        raise SystemExit(
            "No audio sources found. Run scripts/audio-source-probe.py --skip-probe first."
        )

    if index is not None:
        if index < 1 or index > len(queue):
            raise SystemExit(f"--index must be between 1 and {len(queue)}")
        item = queue[index - 1]
        return item.name, item.label

    auto = resolve_audio_device(cfg)
    if auto:
        for item in queue:
            if item.name == auto:
                return item.name, item.label
        return auto, "Auto (capture dongle)"

    item = queue[0]
    return item.name, item.label


def _list_sources() -> int:
    cfg = default_capture_config()
    queue = build_audio_test_queue(
        enumerate_audio_sources(),
        include_auto_resolved=resolve_audio_device(cfg),
        auto_label="Auto (capture dongle)",
    )
    print("Available audio sources for interactive test:\n")
    for index, item in enumerate(queue, start=1):
        print(f"  {index}. {item.label}")
        print(f"     name: {item.name}")
    print(
        "\nStart playback:\n"
        "  python3 scripts/audio-source-test-interactive.py --index N\n"
        "  python3 scripts/audio-source-test-interactive.py --source 'SOURCE_NAME'\n"
        "\nStop playback:\n"
        "  python3 scripts/audio-source-test-stop.py"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Play one capture audio source continuously until stopped",
    )
    parser.add_argument(
        "--source",
        default="",
        help="PulseAudio/PipeWire source name (default: auto-resolved capture dongle)",
    )
    parser.add_argument(
        "--index",
        type=int,
        default=None,
        help="Pick source by number from --list",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List sources and exit",
    )
    args = parser.parse_args()

    if args.list:
        return _list_sources()

    source_name, source_label = _resolve_source_name(
        source=args.source,
        index=args.index,
    )
    pipeline = build_pulsesrc_audition_pipeline(source_name)
    cmd = ["gst-launch-1.0", "-q"] + pipeline.split()

    _stop_existing()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    Path(AUDITION_PID_FILE).write_text(f"{proc.pid}\n", encoding="utf-8")

    time.sleep(0.25)
    if proc.poll() is not None:
        err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace").strip()
        Path(AUDITION_PID_FILE).unlink(missing_ok=True)
        print(f"Failed to start playback for {source_name}", file=sys.stderr)
        if err:
            print(err, file=sys.stderr)
        return 1

    print("=== Android TV Connect interactive audio test ===\n")
    print(f"Source: {source_label}")
    print(f"Device: {source_name}")
    print(f"PID:    {proc.pid} (saved to {AUDITION_PID_FILE})\n")
    print("Audio is playing continuously.")
    print("Reply STOP or YES in Cursor chat when you hear HDMI capture audio.")
    print("Or run: python3 scripts/audio-source-test-stop.py\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
