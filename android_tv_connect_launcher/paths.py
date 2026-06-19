"""Install layout paths and symlink management."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .constants import CURRENT_LINK, DATA_ROOT, INSTALLED_META, VERSIONS_DIR
from .version import InstalledVersion


def ensure_layout() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)


def version_dir(version: str) -> Path:
    safe = version.strip().removeprefix("v").replace("/", "-")
    return VERSIONS_DIR / safe


def read_installed_version() -> InstalledVersion:
    if INSTALLED_META.is_file():
        try:
            raw = json.loads(INSTALLED_META.read_text(encoding="utf-8"))
            version = str(raw.get("version", "0.0.0"))
            code = int(raw.get("versionCode", raw.get("version_code", 0)))
            return InstalledVersion(version=version, version_code=code)
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    current = resolve_current_root()
    if current is not None:
        return read_version_from_tree(current)

    return InstalledVersion(version="0.0.0", version_code=0)


def read_version_from_tree(root: Path) -> InstalledVersion:
    version = "0.0.0"
    version_code = 0

    version_file = root / "VERSION"
    if version_file.is_file():
        version = version_file.read_text(encoding="utf-8").strip() or version

    code_file = root / "VERSION_CODE"
    if code_file.is_file():
        try:
            version_code = int(code_file.read_text(encoding="utf-8").strip())
        except ValueError:
            pass

    return InstalledVersion(version=version, version_code=version_code)


def write_installed_meta(version: str, version_code: int) -> None:
    ensure_layout()
    payload = {"version": version, "versionCode": version_code}
    INSTALLED_META.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def resolve_current_root() -> Path | None:
    if not CURRENT_LINK.exists():
        return None
    target = CURRENT_LINK.resolve()
    if target.is_dir():
        return target
    return None


def app_pythonpath() -> str | None:
    root = resolve_current_root()
    if root is None:
        return None
    return str(root)


def set_current_symlink(version: str) -> Path:
    target = version_dir(version)
    if not target.is_dir():
        raise FileNotFoundError(f"Version directory not found: {target}")

    ensure_layout()
    temp_link = DATA_ROOT / ".current.tmp"
    if temp_link.exists() or temp_link.is_symlink():
        temp_link.unlink()

    os.symlink(target, temp_link)
    temp_link.replace(CURRENT_LINK)
    return target


def remove_version_dir(version: str) -> None:
    path = version_dir(version)
    if path.is_dir():
        shutil.rmtree(path)
