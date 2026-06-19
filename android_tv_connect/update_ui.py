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


def _launcher_command(*extra: str) -> list[str]:
    launcher_root = Path.home() / ".local" / "share" / "android-tv-connect" / "launcher"
    if launcher_root.is_dir():
        env_root = str(launcher_root)
        return [
            sys.executable,
            "-m",
            "android_tv_connect_launcher",
            *extra,
        ], env_root

    repo_root = Path(__file__).resolve().parent.parent
    if (repo_root / "android_tv_connect_launcher").is_dir():
        return [
            sys.executable,
            "-m",
            "android_tv_connect_launcher",
            *extra,
        ], str(repo_root)

    atv = shutil.which("atv-connect")
    if atv:
        return [atv, *extra], ""

    return [sys.executable, "-m", "android_tv_connect_launcher", *extra], str(repo_root)


def check_for_updates(*, apply: bool = False) -> UpdateCheckResponse:
    args = ["--check-updates", "--json", "--force-check"]
    if apply:
        args.append("--apply-updates")

    cmd, pythonpath_root = _launcher_command(*args)
    env = os.environ.copy()
    if pythonpath_root:
        env["PYTHONPATH"] = pythonpath_root + os.pathsep + env.get("PYTHONPATH", "")

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
