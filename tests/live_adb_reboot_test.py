#!/usr/bin/env python3
"""Live test: FUSA reboot → ADB should auto-reconnect."""

from __future__ import annotations

import subprocess
import sys
import time

from android_tv_connect.adb_client import AdbClient

SERIAL = "FUSA2541006925"
MAX_WAIT_S = 180
POLL_S = 2


def main() -> int:
    client = AdbClient(wired_serial=SERIAL)
    if not client.connect():
        print("FAIL: device not connected before reboot test")
        return 1

    print("OK: connected before reboot")
    subprocess.run(["adb", "-s", SERIAL, "reboot"], check=False, capture_output=True)
    print("reboot command sent — monitoring ADB reconnect…")

    seen_down = False
    reconnected_at: int | None = None

    for tick in range(MAX_WAIT_S // POLL_S):
        elapsed = tick * POLL_S
        ok = client.is_connected()
        devices = subprocess.run(
            ["adb", "devices", "-l"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip().splitlines()
        device_line = next((l for l in devices if SERIAL in l), "(absent)")
        print(f"  t={elapsed:3d}s  connected={ok}  adb: {device_line}")

        if not ok:
            seen_down = True
        elif seen_down:
            reconnected_at = elapsed
            break
        time.sleep(POLL_S)

    if reconnected_at is not None:
        print(f"PASS: ADB reconnected {reconnected_at}s after boot completed")
        client.disconnect()
        return 0

    print(f"FAIL: ADB did not reconnect within {MAX_WAIT_S}s")
    client.disconnect()
    return 1


if __name__ == "__main__":
    sys.exit(main())
