"""Detect whether the UI process is already running."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .branding import APP_ID as APP_DBUS_NAME

CACHE_DIR = Path.home() / ".cache" / "android-tv-connect"
LOCK_PATH = CACHE_DIR / "ui.lock"
USER_QUIT_PATH = CACHE_DIR / "user-quit"
WATCH_SERVICE = "android-tv-connect-watch.service"


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


def note_user_quit() -> None:
    """Mark that the user closed the app; the watcher skips auto-launch until cleared."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    USER_QUIT_PATH.write_text("1\n")


def clear_user_quit() -> None:
    """Allow the watcher to auto-launch again (manual launch or new watch session)."""
    try:
        USER_QUIT_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def is_user_quit_active() -> bool:
    """Return True when the user explicitly quit during the current watch session."""
    return USER_QUIT_PATH.is_file()


def stop_watch_service() -> bool:
    """Stop the systemd user watcher."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "stop", WATCH_SERVICE],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
