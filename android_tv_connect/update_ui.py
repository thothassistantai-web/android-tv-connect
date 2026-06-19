"""Delegate update checks to the isolated launcher subprocess."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UpdateCheckResponse:
    ok: bool
    update_available: bool
    mandatory: bool
    installed_version: str
    installed_version_code: int
    manifest_version: str = ""
    manifest_version_code: int = 0
    release_notes: str = ""
    error: str = ""


def _resolve_launcher_root() -> Path | None:
    """Match launcher_dir() / install-local.sh: app_home checkout, then launcher copy."""
    try:
        from android_tv_connect_launcher.paths import launcher_dir

        root = launcher_dir()
        if (root / "android_tv_connect_launcher").is_dir():
            return root
    except ImportError:
        pass

    repo_root = Path(__file__).resolve().parent.parent
    if (repo_root / "android_tv_connect_launcher").is_dir():
        return repo_root

    default_launcher = (
        Path.home() / ".local" / "share" / "android-tv-connect" / "launcher"
    )
    if (default_launcher / "android_tv_connect_launcher").is_dir():
        return default_launcher

    return None


def _launcher_command(*extra: str) -> tuple[list[str], dict[str, str]]:
    launcher_root = _resolve_launcher_root()
    env: dict[str, str] = {}

    if launcher_root is not None:
        env["PYTHONPATH"] = str(launcher_root)
        try:
            from android_tv_connect_launcher.paths import resolve_data_root

            env["ATV_CONNECT_HOME"] = str(resolve_data_root())
        except ImportError:
            pass
        return [
            sys.executable,
            "-m",
            "android_tv_connect_launcher",
            *extra,
        ], env

    atv = shutil.which("atv-connect")
    if atv:
        return [atv, *extra], env

    repo_root = Path(__file__).resolve().parent.parent
    env["PYTHONPATH"] = str(repo_root)
    return [sys.executable, "-m", "android_tv_connect_launcher", *extra], env


def check_for_updates(*, apply: bool = False) -> UpdateCheckResponse:
    args = ["--check-updates", "--json", "--force-check"]
    if apply:
        args.append("--apply-updates")

    cmd, launcher_env = _launcher_command(*args)
    env = os.environ.copy()
    for key, value in launcher_env.items():
        if key == "PYTHONPATH" and key in env:
            env[key] = value + os.pathsep + env[key]
        else:
            env[key] = value

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return UpdateCheckResponse(
            ok=False,
            update_available=False,
            mandatory=False,
            installed_version="",
            installed_version_code=0,
            error=str(exc),
        )

    stdout = proc.stdout.strip()
    if not stdout:
        return UpdateCheckResponse(
            ok=False,
            update_available=False,
            mandatory=False,
            installed_version="",
            installed_version_code=0,
            error=proc.stderr.strip() or f"Launcher exited with code {proc.returncode}",
        )

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return UpdateCheckResponse(
            ok=False,
            update_available=False,
            mandatory=False,
            installed_version="",
            installed_version_code=0,
            error=stdout or proc.stderr.strip(),
        )

    manifest = payload.get("manifest") or {}
    return UpdateCheckResponse(
        ok=not payload.get("error"),
        update_available=bool(payload.get("updateAvailable")),
        mandatory=bool(payload.get("mandatory")),
        installed_version=str(payload.get("installedVersion", "")),
        installed_version_code=int(payload.get("installedVersionCode", 0)),
        manifest_version=str(manifest.get("version", "")),
        manifest_version_code=int(manifest.get("versionCode", 0)),
        release_notes=str(manifest.get("releaseNotes", "")),
        error=str(payload.get("error") or ""),
    )
