"""Detect whether the UI process is already running."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .branding import APP_ID as APP_DBUS_NAME
LOCK_PATH = Path.home() / ".cache" / "android-tv-connect" / "ui.lock"


def _lock_pid() -> int | None:
    try:
        text = LOCK_PATH.read_text().strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cleanup_stale_lock() -> None:
    """Remove a leftover lock file when the owning process is gone."""
    pid = _lock_pid()
    if pid is not None and _pid_alive(pid):
        return
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _dbus_name_owned(name: str) -> bool:
    try:
        out = subprocess.check_output(
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
                name,
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        return "(true)" in out
    except (OSError, subprocess.SubprocessError):
        return False


def _ui_process_argv(pid: int) -> str | None:
  try:
      raw = Path(f"/proc/{pid}/cmdline").read_bytes()
  except OSError:
      return None
  return raw.replace(b"\0", b" ").decode(errors="replace").strip()


def _process_running() -> bool:
    result = subprocess.run(
        ["pgrep", "-f", "python3 -m android_tv_connect"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        argv = _ui_process_argv(pid)
        if argv is None:
            continue
        if "--watch" in argv.split():
            continue
        return True
    return False


def is_ui_running() -> bool:
    """Return True when the GTK application is active on the session bus."""
    cleanup_stale_lock()
    if _dbus_name_owned(APP_DBUS_NAME):
        return True
    pid = _lock_pid()
    if pid is not None and _pid_alive(pid):
        return True
    return _process_running()


def note_ui_pid(pid: int | None = None) -> None:
    """Best-effort PID marker for the watcher when D-Bus is unavailable."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(str(pid or os.getpid()) + "\n")


def clear_ui_pid() -> None:
    cleanup_stale_lock()
