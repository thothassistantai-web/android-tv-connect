"""Discover USB and wireless ADB devices for settings dropdowns."""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

_WIRELESS_SERIAL_RE = re.compile(
    r"^(?P<host>[a-zA-Z0-9.\-]+):(?P<port>\d+)$"
)
_DEFAULT_WIRELESS_PORT = 5555
_SUBNET_PROBE_WORKERS = 24
_SUBNET_PROBE_TIMEOUT_S = 0.35


@dataclass(frozen=True)
class WiredDeviceOption:
    serial: str
    description: str


@dataclass(frozen=True)
class WirelessDeviceOption:
    host: str
    port: int
    address: str
    description: str


def _default_run_adb(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["adb", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=8,
    )


def _device_description(serial: str, line: str) -> str:
    extras: list[str] = []
    for token in line.split():
        if token.startswith(("usb:", "product:", "model:", "device:")):
            extras.append(token)
    if extras:
        return f"{serial} ({', '.join(extras)})"
    return serial


def parse_adb_devices_l(
    stdout: str,
) -> tuple[list[WiredDeviceOption], list[WirelessDeviceOption]]:
    """Parse ``adb devices -l`` output into USB and wireless device lists."""
    wired: list[WiredDeviceOption] = []
    wireless: list[WirelessDeviceOption] = []
    seen_wireless: set[str] = set()

    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        if state != "device":
            continue

        match = _WIRELESS_SERIAL_RE.match(serial)
        if match:
            host = match.group("host")
            port = int(match.group("port"))
            address = f"{host}:{port}"
            if address in seen_wireless:
                continue
            seen_wireless.add(address)
            wireless.append(
                WirelessDeviceOption(
                    host=host,
                    port=port,
                    address=address,
                    description=_device_description(address, line),
                )
            )
            continue

        if "usb:" in line:
            wired.append(
                WiredDeviceOption(serial=serial, description=_device_description(serial, line))
            )

    return wired, wireless


def _local_ipv4_networks() -> list[ipaddress.IPv4Network]:
    networks: list[ipaddress.IPv4Network] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
        interface = ipaddress.ip_address(local_ip)
        if interface.is_private:
            octets = local_ip.split(".")
            if len(octets) == 4:
                networks.append(
                    ipaddress.ip_network(f"{octets[0]}.{octets[1]}.{octets[2]}.0/24", strict=False)
                )
    except OSError:
        pass
    networks.append(ipaddress.ip_network("192.168.1.0/24"))
    return networks


def _probe_wireless_host(
    host: str,
    port: int,
    *,
    run_adb: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> WirelessDeviceOption | None:
    address = f"{host}:{port}"
    result = run_adb(["connect", address])
    if result.returncode != 0:
        return None
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if "connected to" not in combined and "already connected" not in combined:
        return None
    state = run_adb(["-s", address, "get-state"])
    if state.returncode != 0 or state.stdout.strip() != "device":
        return None
    return WirelessDeviceOption(
        host=host,
        port=port,
        address=address,
        description=f"{address} (network scan)",
    )


def scan_subnet_for_wireless_adb(
    *,
    port: int = _DEFAULT_WIRELESS_PORT,
    run_adb: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    max_hosts: int = 64,
) -> list[WirelessDeviceOption]:
    """Probe the local /24 subnet for hosts listening on the ADB TCP port."""
    runner = run_adb or _default_run_adb
    candidates: list[str] = []
    seen: set[str] = set()
    for network in _local_ipv4_networks():
        for host in network.hosts():
            ip = str(host)
            if ip in seen:
                continue
            seen.add(ip)
            candidates.append(ip)
            if len(candidates) >= max_hosts:
                break
        if len(candidates) >= max_hosts:
            break

    found: list[WirelessDeviceOption] = []
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=_SUBNET_PROBE_WORKERS) as pool:
        futures = {
            pool.submit(_probe_wireless_host, host, port, run_adb=runner): host
            for host in candidates
        }
        for future in as_completed(futures):
            try:
                option = future.result()
            except Exception:
                continue
            if option is None:
                continue
            with lock:
                if any(item.address == option.address for item in found):
                    continue
                found.append(option)
    found.sort(key=lambda item: item.address)
    return found


def discover_adb_devices(
    *,
    run_adb: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    scan_subnet: bool = True,
    wireless_port: int = _DEFAULT_WIRELESS_PORT,
) -> tuple[list[WiredDeviceOption], list[WirelessDeviceOption]]:
    """Return USB serials and wireless hosts from ``adb devices -l`` and optional LAN scan."""
    runner = run_adb or _default_run_adb
    runner(["start-server"])
    result = runner(["devices", "-l"])
    wired: list[WiredDeviceOption] = []
    wireless: list[WirelessDeviceOption] = []
    if result.returncode == 0:
        wired, wireless = parse_adb_devices_l(result.stdout)

    if scan_subnet:
        known = {item.address for item in wireless}
        for option in scan_subnet_for_wireless_adb(port=wireless_port, run_adb=runner):
            if option.address not in known:
                wireless.append(option)
                known.add(option.address)

    wired.sort(key=lambda item: item.serial)
    wireless.sort(key=lambda item: item.address)
    return wired, wireless
