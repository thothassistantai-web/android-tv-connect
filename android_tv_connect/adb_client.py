"""ADB client for Android TV Connect."""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from typing import Optional

# Soft remote keycodes (Android KeyEvent)
KEYCODE_DPAD_UP = 19
KEYCODE_DPAD_DOWN = 20
KEYCODE_DPAD_LEFT = 21
KEYCODE_DPAD_RIGHT = 22
KEYCODE_DPAD_CENTER = 23
KEYCODE_ENTER = 66
KEYCODE_DEL = 67
KEYCODE_POWER = 26
KEYCODE_HOME = 3
KEYCODE_BACK = 4
KEYCODE_VOLUME_UP = 24
KEYCODE_VOLUME_DOWN = 25
KEYCODE_VOLUME_MUTE = 164
KEYCODE_MEDIA_PLAY_PAUSE = 85
KEYCODE_SETTINGS = 176
KEYCODE_WAKEUP = 224
KEYCODE_APP_SWITCH = 187


class AdbClient:
    """Manage wired/wireless ADB connections and inject Android TV input."""

    DEFAULT_WIRED_SERIAL = "FUSA2541006925"
    DEFAULT_WIRELESS_HOST = "192.168.1.157"
    DEFAULT_WIRELESS_PORT = 5555

    HEALTH_POLL_INTERVAL = 5.0
    HEALTH_POLL_INTERVAL_DISCONNECTED = 2.0
    RATE_LIMIT_PER_SEC = 60
    _RECONNECT_BASE_DELAY = 1.0
    _RECONNECT_MAX_DELAY = 30.0

    def __init__(
        self,
        wired_serial: str = DEFAULT_WIRED_SERIAL,
        wireless_host: str = DEFAULT_WIRELESS_HOST,
        wireless_port: int = DEFAULT_WIRELESS_PORT,
        *,
        prefer_wired: bool = True,
        on_connection_change=None,
    ) -> None:
        self._wired_serial = wired_serial
        self._prefer_wired = prefer_wired
        self._wireless_host = wireless_host
        self._wireless_port = wireless_port
        self._wireless_address = f"{wireless_host}:{wireless_port}"
        self._serial: Optional[str] = None
        self._is_wireless = False
        self._lock = threading.Lock()
        self._command_times: deque[float] = deque()
        self._health_thread: Optional[threading.Thread] = None
        self._health_stop = threading.Event()
        self._reconnect_delay = self._RECONNECT_BASE_DELAY
        self._on_connection_change = on_connection_change
        self._last_notified_connected = False
        self._connected = False

    def update_settings(
        self,
        *,
        wired_serial: str,
        wireless_host: str,
        wireless_port: int,
        prefer_wired: bool,
    ) -> None:
        """Apply new connection settings; reconnect if values changed."""
        with self._lock:
            changed = (
                wired_serial != self._wired_serial
                or wireless_host != self._wireless_host
                or wireless_port != self._wireless_port
                or prefer_wired != self._prefer_wired
            )
            self._wired_serial = wired_serial
            self._wireless_host = wireless_host
            self._wireless_port = wireless_port
            self._prefer_wired = prefer_wired
            self._wireless_address = f"{wireless_host}:{wireless_port}"
        if changed:
            self.disconnect()
            self.connect()

    def connect(self) -> bool:
        """Start ADB server and connect using the configured preference order."""
        with self._lock:
            self._run_adb(["start-server"], check=False)
            order = ("wired", "wireless") if self._prefer_wired else ("wireless", "wired")
            serial: Optional[str] = None
            is_wireless = False
            for kind in order:
                if kind == "wired":
                    serial = self._find_wired_serial()
                    if serial:
                        is_wireless = False
                        break
                else:
                    serial = self._find_wireless_serial()
                    if serial:
                        is_wireless = True
                        break

            if not serial or not self._device_ready(serial):
                self._serial = None
                self._is_wireless = False
                self._stop_health_poll()
                return False

            self._serial = serial
            self._is_wireless = is_wireless
            self._reconnect_delay = self._RECONNECT_BASE_DELAY
            self._start_health_poll()
            self._set_connected(True)
            return True

    def disconnect(self) -> None:
        """Stop health polling and disconnect wireless ADB if active."""
        with self._lock:
            self._stop_health_poll()
            if self._is_wireless and self._serial:
                self._run_adb(["disconnect", self._serial], check=False)
            self._serial = None
            self._is_wireless = False
            self._set_connected(False)

    def is_connected(self) -> bool:
        """Return cached ADB connection state (updated by the health thread)."""
        return self._connected

    def _set_connected(self, connected: bool) -> None:
        if self._connected == connected:
            return
        self._connected = connected
        self._notify_connection_change(connected)

    def tap(self, x: int, y: int) -> bool:
        """Send a tap at screen coordinates."""
        return self._input_command("tap", str(x), str(y))

    def keyevent(self, code: int) -> bool:
        """Send a key event by Android keycode."""
        return self._input_command("keyevent", str(code))

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: int,
    ) -> bool:
        """Send a swipe gesture with duration in milliseconds."""
        return self._input_command(
            "swipe",
            str(x1),
            str(y1),
            str(x2),
            str(y2),
            str(duration),
        )

    def text(self, s: str) -> bool:
        """Send text input with ADB shell escaping."""
        return self._input_command("text", self._escape_text(s))

    def _find_wired_serial(self) -> Optional[str]:
        result = self._run_adb(["devices", "-l"], check=False)
        if result.returncode != 0:
            return None

        auto_discover = not self._wired_serial
        usb_pick: Optional[str] = None
        any_pick: Optional[str] = None
        specific_fallback: Optional[str] = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            if state != "device":
                continue
            if not auto_discover and serial != self._wired_serial:
                continue
            if "usb:" in line:
                if not auto_discover:
                    return serial
                usb_pick = usb_pick or serial
            elif auto_discover:
                any_pick = any_pick or serial
            else:
                specific_fallback = serial
        if auto_discover:
            return usb_pick or any_pick
        return specific_fallback

    def _find_wireless_serial(self) -> Optional[str]:
        from .adb_discovery import parse_adb_devices_l

        result = self._run_adb(["devices", "-l"], check=False)
        if result.returncode == 0:
            _wired, wireless = parse_adb_devices_l(result.stdout)
            if wireless:
                if not self._wireless_host:
                    return wireless[0].address
                for option in wireless:
                    if option.host == self._wireless_host:
                        if option.port == self._wireless_port:
                            return option.address
                        return f"{self._wireless_host}:{self._wireless_port}"

        if not self._wireless_host:
            return None

        connect_result = self._run_adb(["connect", self._wireless_address], check=False)
        if connect_result.returncode != 0:
            return None
        combined = f"{connect_result.stdout}\n{connect_result.stderr}".lower()
        if "connected to" not in combined and "already connected" not in combined:
            return None
        return self._wireless_address

    def _notify_connection_change(self, connected: bool) -> None:
        if connected == self._last_notified_connected:
            return
        self._last_notified_connected = connected
        callback = self._on_connection_change
        if callback is not None:
            callback(connected)

    def _device_ready(self, serial: str) -> bool:
        result = self._run_adb(["-s", serial, "get-state"], check=False)
        return result.returncode == 0 and result.stdout.strip() == "device"

    def _run_adb(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["adb", *args],
            capture_output=True,
            text=True,
            check=check,
            timeout=5,
        )

    def _input_command(self, *args: str) -> bool:
        self._wait_for_rate_limit()
        with self._lock:
            if not self._serial:
                return False
            serial = self._serial

        result = self._run_adb(
            ["-s", serial, "shell", "input", *args],
            check=False,
        )
        return result.returncode == 0

    def _wait_for_rate_limit(self) -> None:
        now = time.monotonic()
        window = 1.0
        while self._command_times and now - self._command_times[0] >= window:
            self._command_times.popleft()

        if len(self._command_times) >= self.RATE_LIMIT_PER_SEC:
            sleep_for = window - (now - self._command_times[0])
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
            while self._command_times and now - self._command_times[0] >= window:
                self._command_times.popleft()

        self._command_times.append(time.monotonic())

    @staticmethod
    def _escape_text(text: str) -> str:
        escaped = []
        for char in text:
            if char == " ":
                escaped.append("%s")
            elif char == "%":
                escaped.append("%%")
            elif char in "&;|<>()$\\'\"`":
                escaped.append("\\" + char)
            else:
                escaped.append(char)
        return "".join(escaped)

    def _start_health_poll(self) -> None:
        if self._health_thread and self._health_thread.is_alive():
            return
        self._health_stop.clear()
        self._health_thread = threading.Thread(
            target=self._health_poll_loop,
            name="adb-health-poll",
            daemon=True,
        )
        self._health_thread.start()

    def _stop_health_poll(self) -> None:
        self._health_stop.set()
        if self._health_thread and self._health_thread.is_alive():
            self._health_thread.join(timeout=self.HEALTH_POLL_INTERVAL + 1.0)
        self._health_thread = None

    def _health_poll_loop(self) -> None:
        while not self._health_stop.wait(self._poll_interval()):
            with self._lock:
                serial = self._serial

            if serial and self._device_ready(serial):
                self._reconnect_delay = self._RECONNECT_BASE_DELAY
                self._set_connected(True)
                continue

            self._set_connected(False)
            if self._attempt_reconnect():
                self._set_connected(True)
                continue

            self._reconnect_delay = min(
                self._reconnect_delay * 2,
                self._RECONNECT_MAX_DELAY,
            )
            self._health_stop.wait(self._reconnect_delay)

    def _poll_interval(self) -> float:
        if self._connected:
            return self.HEALTH_POLL_INTERVAL
        return self.HEALTH_POLL_INTERVAL_DISCONNECTED

    def _attempt_reconnect(self) -> bool:
        self._run_adb(["start-server"], check=False)

        order = ("wired", "wireless") if self._prefer_wired else ("wireless", "wired")
        serial: Optional[str] = None
        is_wireless = False
        for kind in order:
            if kind == "wired":
                serial = self._find_wired_serial()
                if serial:
                    is_wireless = False
                    break
            else:
                serial = self._find_wireless_serial()
                if serial:
                    is_wireless = True
                    break

        if not serial or not self._device_ready(serial):
            with self._lock:
                self._serial = None
                self._is_wireless = False
            return False

        with self._lock:
            self._serial = serial
            self._is_wireless = is_wireless
        self._reconnect_delay = self._RECONNECT_BASE_DELAY
        return True
