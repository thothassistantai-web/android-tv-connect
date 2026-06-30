"""Launcher entry point — update check then spawn the main app."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys

from . import LAUNCHER_VERSION
from .paths import app_pythonpath, current_link, read_installed_version, resolve_current_root
from .settings import load_launcher_settings
from .updater import apply_update, check_for_update, should_prompt

LOG = logging.getLogger(__name__)


def _build_app_env() -> dict[str, str]:
    env = os.environ.copy()
    root = app_pythonpath()
    if root:
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _spawn_app(argv: list[str]) -> int:
    root = resolve_current_root()
    if root is None:
        msg = f"No installed app bundle found under {current_link()}"
        LOG.error(msg)
        _show_fatal_error(msg)
        return 1

    cmd = [sys.executable, "-m", "android_tv_connect", *argv]
    LOG.info("Launching app from %s", root)
    try:
        return subprocess.call(cmd, env=_build_app_env())
    except OSError as exc:
        msg = f"Failed to start Android TV Connect: {exc}"
        LOG.error(msg)
        _show_fatal_error(msg)
        return 1


def _show_fatal_error(message: str) -> None:
    """Best-effort GUI error when the launcher cannot spawn the app."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    try:
        subprocess.run(
            [
                "zenity",
                "--error",
                "--title=Android TV Connect",
                f"--text={message}",
                "--width=420",
            ],
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _run_update_flow(*, apply: bool, force_check: bool) -> UpdateCheckResult:
    from .updater import UpdateCheckResult

    settings = load_launcher_settings()
    if not force_check and not settings.auto_check_updates:
        return UpdateCheckResult(
            installed=read_installed_version(),
            manifest=None,
            update_available=False,
            mandatory=False,
        )

    result = check_for_update(settings=settings)
    if result.error:
        LOG.warning("Update check failed: %s", result.error)
        return result

    if result.update_available and result.manifest is not None:
        if apply or result.mandatory or should_prompt(result, settings):
            LOG.info(
                "Installing update %s (versionCode %s)",
                result.manifest.version,
                result.manifest.version_code,
            )
            apply_update(result.manifest)
            result = check_for_update(settings=settings)
    return result


def _print_versions() -> None:
    installed = read_installed_version()
    print(f"Android TV Connect Launcher {LAUNCHER_VERSION}")
    print(f"Installed app {installed.version} (versionCode {installed.version_code})")


def _print_json(result) -> None:
    payload = {
        "launcherVersion": LAUNCHER_VERSION,
        "installedVersion": result.installed.version,
        "installedVersionCode": result.installed.version_code,
        "updateAvailable": result.update_available,
        "mandatory": result.mandatory,
        "error": result.error,
    }
    if result.manifest is not None:
        payload["manifest"] = {
            "version": result.manifest.version,
            "versionCode": result.manifest.version_code,
            "bundleUrl": result.manifest.bundle_url,
            "sha256": result.manifest.sha256,
            "mandatory": result.manifest.mandatory,
            "releaseNotes": result.manifest.release_notes,
        }
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Android TV Connect launcher (updates + app spawn)",
    )
    parser.add_argument(
        "--check-updates",
        action="store_true",
        help="Check for updates (respects auto-check setting unless --force-check)",
    )
    parser.add_argument(
        "--force-check",
        action="store_true",
        help="Run update check even when auto-check on launch is disabled",
    )
    parser.add_argument(
        "--apply-updates",
        action="store_true",
        help="Download and install an available update",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="Skip update check before launching the app",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print update check result as JSON (with --check-updates)",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show launcher and installed app versions",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Forward to main app watch mode after update check",
    )
    parser.add_argument(
        "app_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to android_tv_connect",
    )
    args = parser.parse_args(argv)

    if args.version:
        _print_versions()
        return 0

    check_only = args.check_updates and not args.watch and not args.app_args
    if check_only or args.apply_updates:
        result = _run_update_flow(
            apply=args.apply_updates,
            force_check=args.force_check or args.apply_updates,
        )
        if args.json or check_only:
            _print_json(result)
        if result.error and args.apply_updates:
            return 1
        if check_only and not args.apply_updates:
            return 0 if not result.error else 1

    if args.no_update_check:
        pass
    elif not check_only:
        _run_update_flow(apply=True, force_check=False)

    forward = []
    if args.watch:
        forward.append("--watch")
    forward.extend(args.app_args)

    if not forward and check_only:
        return 0

    return _spawn_app(forward)


if __name__ == "__main__":
    raise SystemExit(main())
