"""Device watcher and application entry point."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from subprocess import TimeoutExpired

from .adb_client import AdbClient
from .branding import APP_NAME, VERSION
from .capture_device import is_capture_device_available
from .settings_store import load_config
from .singleton import (
    cleanup_stale_lock,
    clear_ui_pid,
    clear_user_quit,
    is_ui_running,
    is_user_quit_active,
    note_ui_pid,
    note_user_quit,
    stop_watch_service,
)
from .window import AndroidTvApp

LOG = logging.getLogger(__name__)

_LAUNCH_COOLDOWN_S = 8.0
_last_launch_at = 0.0


def should_auto_launch_ui(
    *,
    devices_ready: bool,
    ui_running: bool,
    user_quit: bool,
    seconds_since_last_launch: float,
    cooldown_s: float = _LAUNCH_COOLDOWN_S,
) -> bool:
    """Whether watch mode should spawn the GTK app."""
    return (
        devices_ready
        and not ui_running
        and not user_quit
        and seconds_since_last_launch >= cooldown_s
    )


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

    clear_user_quit()

    if not config.watch_autostart_enabled:
        LOG.info("Watch mode disabled in settings")
        while True:
            time.sleep(max(poll, 5.0))

    LOG.info("Watch mode started (poll=%.1fs)", poll)
    missing_since: float | None = None

    while True:
        try:
            ready = _both_ready(config)
        except TimeoutExpired:
            LOG.warning("ADB connect timed out during watch poll")
            ready = False
        running = is_ui_running()
        user_quit = is_user_quit_active()
        now = time.monotonic()

        if should_auto_launch_ui(
            devices_ready=ready,
            ui_running=running,
            user_quit=user_quit,
            seconds_since_last_launch=now - _last_launch_at,
        ):
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


def run_quit() -> int:
    """Fully quit: suppress watcher auto-launch and stop the watch service."""
    note_user_quit()
    if stop_watch_service():
        LOG.info("Stopped %s", "android-tv-connect-watch.service")
    else:
        LOG.info("Watch service not running or systemctl unavailable")
    return 0


def run_app() -> int:
    cleanup_stale_lock()
    clear_user_quit()
    app = AndroidTvApp()
    note_ui_pid()
    try:
        return app.run(sys.argv)
    finally:
        clear_ui_pid()


def _run_diag_client(argv: list[str]) -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(root, "scripts", "atv-diag.py")
    if not os.path.isfile(script):
        print("atv-diag.py not found; reinstall Android TV Connect", file=sys.stderr)
        return 1
    result = subprocess.run(
        [sys.executable, script, *argv],
        check=False,
    )
    return int(result.returncode)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--diag":
        return _run_diag_client(argv[1:])

    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch for capture + ADB and auto-launch the UI",
    )
    parser.add_argument(
        "--quit",
        action="store_true",
        help="Quit and stop auto-launch until you run atv-connect again or restart the watcher",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {VERSION}",
    )
    args = parser.parse_args(argv)

    if args.quit:
        return run_quit()

    if args.watch:
        try:
            run_watch()
        except KeyboardInterrupt:
            return 0
        return 0

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
