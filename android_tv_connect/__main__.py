"""Device watcher and application entry point."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time

from .adb_client import AdbClient
from .branding import APP_NAME, VERSION
from .capture_device import is_capture_device_available
from .settings_store import load_config
from .singleton import cleanup_stale_lock, clear_ui_pid, is_ui_running, note_ui_pid
from .window import AndroidTvApp

LOG = logging.getLogger(__name__)

_LAUNCH_COOLDOWN_S = 8.0
_last_launch_at = 0.0


def _adb_ready(config) -> bool:
    client = AdbClient(
        wired_serial=config.adb.wired_serial,
        wireless_host=config.adb.wireless_host,
        wireless_port=config.adb.wireless_port,
        prefer_wired=config.input.prefer_wired_adb,
    )
    if client.connect():
        client.disconnect()
        return True
    return False


def _both_ready(config) -> bool:
    return is_capture_device_available(config.capture) and _adb_ready(config)


def run_watch() -> int:
    global _last_launch_at
    config = load_config()
    poll = config.watch_poll_interval_s
    debounce = config.watch_disconnect_debounce_s

    if not config.watch_autostart_enabled:
        LOG.info("Watch mode disabled in settings")
        while True:
            time.sleep(max(poll, 5.0))

    LOG.info("Watch mode started (poll=%.1fs)", poll)
    missing_since: float | None = None

    while True:
        ready = _both_ready(config)
        running = is_ui_running()
        now = time.monotonic()

        if ready and not running and (now - _last_launch_at) >= _LAUNCH_COOLDOWN_S:
            LOG.info("Devices ready — launching UI")
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env = os.environ.copy()
            env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
            subprocess.Popen(
                [sys.executable, "-m", "android_tv_connect"],
                env=env,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _last_launch_at = now
            missing_since = None
        elif not ready:
            if missing_since is None:
                missing_since = now
        else:
            missing_since = None

        if missing_since is not None and now - missing_since > debounce:
            LOG.debug(
                "Devices not ready (capture=%s)",
                is_capture_device_available(config.capture),
            )

        time.sleep(poll)


def run_app() -> int:
    cleanup_stale_lock()
    app = AndroidTvApp()
    note_ui_pid()
    try:
        return app.run(sys.argv)
    finally:
        clear_ui_pid()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch for capture + ADB and auto-launch the UI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {VERSION}",
    )
    args = parser.parse_args(argv)

    if args.watch:
        try:
            run_watch()
        except KeyboardInterrupt:
            return 0
        return 0

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
