"""scrcpy screen mirroring integrated with the app's ADB session."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from typing import Callable, Optional

from .adb_client import AdbClient
from .config import AppConfig, ScrcpyConfig

LOG = logging.getLogger(__name__)

SCRCPY_INSTALL_HINT = (
    "Install scrcpy: sudo apt install scrcpy\n"
    "Or set a custom path in Settings → Screen mirror (scrcpy)."
)


def resolve_scrcpy_command(scrcpy_path: str) -> str:
    """Return the executable name or path used to launch scrcpy."""
    stripped = scrcpy_path.strip()
    return stripped or "scrcpy"


def is_scrcpy_available(scrcpy_path: str = "") -> bool:
    """Return True when scrcpy can be launched."""
    command = resolve_scrcpy_command(scrcpy_path)
    if os.path.sep in command or command.startswith("."):
        return os.path.isfile(command) and os.access(command, os.X_OK)
    return shutil.which(command) is not None


def build_scrcpy_argv(scrcpy: ScrcpyConfig, serial: str) -> list[str]:
    """Build scrcpy command-line arguments for the given ADB serial."""
    command = resolve_scrcpy_command(scrcpy.scrcpy_path)
    argv = [command, f"--serial={serial}"]

    if scrcpy.max_size > 0:
        argv.append(f"--max-size={scrcpy.max_size}")
    if scrcpy.bit_rate.strip():
        argv.append(f"--video-bit-rate={scrcpy.bit_rate.strip()}")
    if scrcpy.no_audio:
        argv.append("--no-audio")
    if scrcpy.stay_awake:
        argv.append("--stay-awake")
    if scrcpy.turn_screen_off:
        argv.append("--turn-screen-off")
    if scrcpy.fullscreen:
        argv.append("--fullscreen")
    title = scrcpy.window_title.strip()
    if title:
        argv.append(f"--window-title={title}")

    return argv


def resolve_scrcpy_target(
    config: AppConfig,
    adb: AdbClient,
    *,
    ensure_connected: bool = True,
) -> tuple[list[str] | None, str | None]:
    """Resolve scrcpy argv using the app's active ADB target.

    Returns ``(argv, error)`` where ``argv`` is set on success and ``error`` on failure.
    """
    if not is_scrcpy_available(config.scrcpy.scrcpy_path):
        return None, f"scrcpy not found on PATH.\n\n{SCRCPY_INSTALL_HINT}"

    if not adb.is_connected():
        if not ensure_connected:
            return None, "ADB not connected — connect a device first."
        if not adb.connect():
            return None, "ADB connection failed — check Settings or click the ADB chip."

    serial = adb.active_serial()
    if not serial:
        return None, "No active ADB device serial."

    argv = build_scrcpy_argv(config.scrcpy, serial)
    return argv, None


class ScrcpySession:
    """Background scrcpy subprocess with optional log forwarding."""

    def __init__(
        self,
        *,
        on_state_change: Callable[[bool], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._log_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._on_state_change = on_state_change
        self._on_log = on_log

    def is_running(self) -> bool:
        with self._lock:
            proc = self._process
        if proc is None:
            return False
        if proc.poll() is not None:
            self._clear_process()
            return False
        return True

    def stop(self) -> None:
        with self._lock:
            proc = self._process
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
        self._clear_process()

    def launch(self, argv: list[str]) -> tuple[bool, str | None]:
        """Start scrcpy with the given argv. Returns ``(ok, error)``."""
        if self.is_running():
            return False, "scrcpy is already running."

        LOG.info("Launching scrcpy: %s", " ".join(argv))
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError:
            return False, f"scrcpy executable not found.\n\n{SCRCPY_INSTALL_HINT}"
        except OSError as exc:
            return False, f"Failed to start scrcpy: {exc}"

        with self._lock:
            self._process = proc

        self._log_thread = threading.Thread(
            target=self._drain_output,
            args=(proc,),
            name="scrcpy-log",
            daemon=True,
        )
        self._log_thread.start()
        self._notify_state(True)
        return True, None

    def active_transport_label(self, adb: AdbClient) -> str:
        """Human-readable transport for UI tooltips."""
        if adb.is_wireless_active():
            return "wireless"
        return "wired"

    def _drain_output(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.rstrip()
            if text:
                LOG.info("scrcpy: %s", text)
                if self._on_log is not None:
                    self._on_log(text)
        proc.wait()
        self._clear_process()
        self._notify_state(False)

    def _clear_process(self) -> None:
        with self._lock:
            self._process = None

    def _notify_state(self, running: bool) -> None:
        callback = self._on_state_change
        if callback is not None:
            callback(running)
