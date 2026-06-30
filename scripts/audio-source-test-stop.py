#!/usr/bin/env python3
"""Stop the background interactive audio test player."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from android_tv_connect.audio_source_test import AUDITION_PID_FILE


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


def stop_player(*, quiet: bool = False) -> int:
    pid = _read_pid()
    if pid is None:
        if not quiet:
            print("No interactive audio test is running (PID file missing).")
        return 1

    if not _is_running(pid):
        Path(AUDITION_PID_FILE).unlink(missing_ok=True)
        if not quiet:
            print(f"Stale PID file removed (process {pid} not running).")
        return 1

    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            if not _is_running(pid):
                break
            time.sleep(0.05)
        if _is_running(pid):
            os.kill(pid, signal.SIGKILL)
    except OSError as exc:
        print(f"Failed to stop process {pid}: {exc}", file=sys.stderr)
        return 1
    finally:
        Path(AUDITION_PID_FILE).unlink(missing_ok=True)

    if not quiet:
        print(f"Stopped interactive audio test (PID {pid}).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop interactive audio test playback")
    parser.add_argument("--quiet", action="store_true", help="Exit silently when idle")
    args = parser.parse_args()
    return stop_player(quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
