"""Connection status labels, mismatch warnings, and hot-plug heuristics."""

from __future__ import annotations

LEGACY_WIRED_SERIAL = "FUSA2541006925"
LEGACY_WIRELESS_HOST = "192.168.1.157"


def _short_label(value: str, *, limit: int = 14) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def format_adb_chip_label(
    *,
    connected: bool,
    serial: str | None,
    is_wireless: bool,
) -> str:
    if not connected or not serial:
        return "ADB"
    short = _short_label(serial)
    if is_wireless:
        return f"ADB · {short} (Wi‑Fi)"
    return f"ADB · {short} (USB)"


def format_adb_chip_tooltip(
    *,
    connected: bool,
    serial: str | None,
    is_wireless: bool,
    action_hint: str,
) -> str:
    if not connected:
        return "ADB offline — use Refresh & connect"
    transport = "wireless" if is_wireless else "wired USB"
    return f"{serial} via {transport} — {action_hint}"


def format_capture_chip_label(
    *,
    device: str | None,
    playing: bool,
) -> str:
    if not device:
        return "Capture · none"
    short = _short_label(device.replace("/dev/", ""))
    if playing:
        return f"Capture · {short}"
    return f"Capture · {short} (idle)"


def format_capture_chip_tooltip(
    *,
    device: str | None,
    playing: bool,
    user_paused: bool,
    state: str,
) -> str:
    node = device or "no capture device"
    if playing:
        return f"{node} — live video, click to pause"
    if user_paused:
        return f"{node} — paused, click to resume"
    if state == "disconnected":
        return f"{node} — USB capture disconnected"
    return f"{node} — click to reconnect capture"


def capture_adb_mismatch_warning(
    *,
    capture_usb_present: bool,
    adb_connected: bool,
    adb_serial: str | None,
    adb_is_wireless: bool,
    usb_serials: list[str],
    wireless_count: int,
) -> str | None:
    """Simple heuristic: capture dongle present but ADB may target the wrong device."""
    if not capture_usb_present or not adb_connected:
        return None

    device_count = len(usb_serials) + wireless_count
    if device_count < 2:
        return None

    if adb_is_wireless and usb_serials:
        return (
            "HDMI capture is active but ADB is using wireless while USB devices "
            "are also available. Confirm the correct target in Settings."
        )

    if adb_serial and adb_serial in usb_serials and len(usb_serials) >= 2:
        others = [serial for serial in usb_serials if serial != adb_serial]
        if others:
            return (
                f"Multiple USB Android devices detected ({', '.join(usb_serials)}). "
                f"ADB is on {adb_serial} — confirm this matches your capture setup."
            )

    if adb_serial and adb_serial not in usb_serials and not adb_is_wireless:
        return (
            f"ADB target {adb_serial} is not among visible USB devices. "
            "Use Refresh & connect or pick a device in Settings."
        )

    return None


def detect_hotplug_switch(
    *,
    previous_usb: set[str],
    current_usb: set[str],
    watch_serial: str | None,
    dismissed: set[str],
) -> str | None:
    """Offer switching when one new USB device appears and the watched serial vanished."""
    if not watch_serial or watch_serial in dismissed:
        return None
    if watch_serial in current_usb:
        return None
    if watch_serial not in previous_usb:
        return None

    new_devices = current_usb - previous_usb
    if len(new_devices) != 1:
        return None
    new_serial = next(iter(new_devices))
    if new_serial in dismissed:
        return None
    return new_serial


def connection_toast_message(serial: str, *, is_wireless: bool) -> str:
    transport = "Wi‑Fi" if is_wireless else "USB"
    return f"Connected to {serial} via {transport}"
