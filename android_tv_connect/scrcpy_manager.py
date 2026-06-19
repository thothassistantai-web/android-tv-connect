"""scrcpy screen mirroring integrated with the app's ADB session."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .adb_client import AdbClient
from .config import AppConfig, ScrcpyConfig

LOG = logging.getLogger(__name__)

SCRCPY_INSTALL_HINT = (
    "Install scrcpy: sudo apt install scrcpy\n"
    "Or set a custom path in Settings → Screen mirror (scrcpy)."
)

SCRCPY_STARTUP_GRACE_S = 0.6
SCRCPY_QUICK_EXIT_THRESHOLD_S = 2.0
SCRCPY_RELAUNCH_COOLDOWN_S = 15.0

_VERSION_RE = re.compile(r"scrcpy\s+(\d+)\.(\d+)", re.IGNORECASE)
_capabilities_cache: dict[str, ScrcpyCapabilities | None] = {}


@dataclass(frozen=True)
class ScrcpyCapabilities:
    """Feature flags derived from the installed scrcpy binary."""

    major: int
    minor: int

    @property
    def uses_video_bit_rate(self) -> bool:
        return (self.major, self.minor) >= (2, 0)

    @property
    def supports_no_audio(self) -> bool:
        return (self.major, self.minor) >= (2, 0)


def resolve_scrcpy_command(scrcpy_path: str) -> str:
    """Return the executable name or path used to launch scrcpy."""
    stripped = scrcpy_path.strip()
    return stripped or "scrcpy"


def clear_scrcpy_capabilities_cache() -> None:
    """Clear cached scrcpy version probes (for tests)."""
    _capabilities_cache.clear()


def probe_scrcpy_capabilities(scrcpy_path: str = "") -> ScrcpyCapabilities | None:
    """Return scrcpy version capabilities, or None when probing fails."""
    command = resolve_scrcpy_command(scrcpy_path)
    if command in _capabilities_cache:
        return _capabilities_cache[command]

    try:
        result = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        LOG.debug("scrcpy version probe failed for %s: %s", command, exc)
        _capabilities_cache[command] = None
        return None

    text = (result.stdout or "") + (result.stderr or "")
    match = _VERSION_RE.search(text)
    if not match:
        LOG.debug("scrcpy version probe: no version in output for %s", command)
        _capabilities_cache[command] = None
        return None

    caps = ScrcpyCapabilities(int(match.group(1)), int(match.group(2)))
    _capabilities_cache[command] = caps
    return caps


def is_scrcpy_available(scrcpy_path: str = "") -> bool:
    """Return True when scrcpy can be launched."""
    command = resolve_scrcpy_command(scrcpy_path)
    if os.path.sep in command or command.startswith("."):
        return os.path.isfile(command) and os.access(command, os.X_OK)
    return shutil.which(command) is not None


def build_scrcpy_argv(
    scrcpy: ScrcpyConfig,
    serial: str,
    *,
    capabilities: ScrcpyCapabilities | None = None,
) -> list[str]:
    """Build scrcpy command-line arguments for the given ADB serial."""
    command = resolve_scrcpy_command(scrcpy.scrcpy_path)
    if capabilities is None:
        capabilities = probe_scrcpy_capabilities(scrcpy.scrcpy_path)

    argv = [command, f"--serial={serial}"]

    if scrcpy.max_size > 0:
        argv.append(f"--max-size={scrcpy.max_size}")

    bit_rate = scrcpy.bit_rate.strip()
    if bit_rate:
        if capabilities is not None and capabilities.uses_video_bit_rate:
            argv.append(f"--video-bit-rate={bit_rate}")
        else:
            argv.append(f"--bit-rate={bit_rate}")

    if scrcpy.no_audio and (
        capabilities is None or capabilities.supports_no_audio
    ):
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


def format_scrcpy_exit_message(exit_code: int, log_lines: list[str]) -> str:
    """Build a user-facing message for a short-lived scrcpy failure."""
    snippet = "\n".join(log_lines[-8:]).strip()
    body = f"scrcpy exited immediately (code {exit_code})."
    if snippet:
        body += f"\n\n{snippet}"
    return body


class ScrcpySession:
    """Background scrcpy subprocess with optional log forwarding."""

    def __init__(
        self,
        *,
        on_state_change: Callable[[bool], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_quick_exit: Callable[[int, list[str]], None] | None = None,
        startup_grace_s: float = SCRCPY_STARTUP_GRACE_S,
        quick_exit_threshold_s: float = SCRCPY_QUICK_EXIT_THRESHOLD_S,
    ) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._log_thread: threading.Thread | None = None
        self._watch_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._on_state_change = on_state_change
        self._on_log = on_log
        self._on_quick_exit = on_quick_exit
        self._startup_grace_s = startup_grace_s
        self._quick_exit_threshold_s = quick_exit_threshold_s
        self._started_at = 0.0
        self._log_buffer: list[str] = []
        self._running_notified = False

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
        if self._running_notified:
            self._running_notified = False
            self._notify_state(False)

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

        self._started_at = time.monotonic()
        self._log_buffer = []
        self._running_notified = False

        with self._lock:
            self._process = proc

        self._log_thread = threading.Thread(
            target=self._drain_output,
            args=(proc,),
            name="scrcpy-log",
            daemon=True,
        )
        self._log_thread.start()
        self._watch_thread = threading.Thread(
            target=self._watch_startup,
            args=(proc,),
            name="scrcpy-watch",
            daemon=True,
        )
        self._watch_thread.start()
        return True, None

    def active_transport_label(self, adb: AdbClient) -> str:
        """Human-readable transport for UI tooltips."""
        if adb.is_wireless_active():
            return "wireless"
        return "wired"

    def _watch_startup(self, proc: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + self._startup_grace_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.05)

        if proc.poll() is None and not self._running_notified:
            self._running_notified = True
            self._notify_state(True)

    def _drain_output(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.rstrip()
            if text:
                self._log_buffer.append(text)
                LOG.info("scrcpy: %s", text)
                if self._on_log is not None:
                    self._on_log(text)
        exit_code = proc.wait()
        duration = time.monotonic() - self._started_at
        log_lines = list(self._log_buffer)
        was_running = self._running_notified
        self._clear_process()
        self._running_notified = False

        if (
            duration < self._quick_exit_threshold_s
            and exit_code != 0
            and self._on_quick_exit is not None
        ):
            self._on_quick_exit(exit_code, log_lines)

        if was_running:
            self._notify_state(False)

    def _clear_process(self) -> None:
        with self._lock:
            self._process = None

    def _notify_state(self, running: bool) -> None:
        callback = self._on_state_change
        if callback is not None:
            callback(running)
