"""Install layout paths and symlink management."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .constants import DEFAULT_DATA_ROOT
from .version import InstalledVersion

_CONFIG_PATH = Path.home() / ".config" / "android-tv-connect" / "config.json"


def resolve_data_root() -> Path:
    """Return app install root (env ATV_CONNECT_HOME, config app_home, or default)."""
    env = os.environ.get("ATV_CONNECT_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()

    if _CONFIG_PATH.is_file():
        try:
            raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            home = str(raw.get("app_home", "")).strip()
            if home:
                return Path(home).expanduser().resolve()
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    return DEFAULT_DATA_ROOT


def data_root() -> Path:
    return resolve_data_root()


def versions_dir() -> Path:
    return data_root() / "versions"


def current_link() -> Path:
    return data_root() / "current"


def installed_meta() -> Path:
    return data_root() / "installed.json"


def launcher_dir() -> Path:
    """Directory containing android_tv_connect_launcher on PYTHONPATH."""
    root = data_root()
    if (root / "android_tv_connect_launcher").is_dir():
        return root
    return root / "launcher"


def is_dev_checkout() -> bool:
    """True when app_home points at a source tree (live checkout)."""
    root = data_root()
    return (root / "android_tv_connect").is_dir() and (root / "VERSION").is_file()


def ensure_layout() -> None:
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    versions_dir().mkdir(parents=True, exist_ok=True)


def version_dir(version: str) -> Path:
    safe = version.strip().removeprefix("v").replace("/", "-")
    return versions_dir() / safe


def read_installed_version() -> InstalledVersion:
    meta = installed_meta()
    if meta.is_file():
        try:
            raw = json.loads(meta.read_text(encoding="utf-8"))
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
    installed_meta().write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def resolve_current_root() -> Path | None:
    link = current_link()
    if not link.exists():
        return None
    target = link.resolve()
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

    root = data_root()
    link = current_link()
    ensure_layout()
    temp_link = root / ".current.tmp"
    if temp_link.exists() or temp_link.is_symlink():
        temp_link.unlink()

    os.symlink(target, temp_link)
    temp_link.replace(link)
    return target


def remove_version_dir(version: str) -> None:
    path = version_dir(version)
    if path.is_dir():
        shutil.rmtree(path)
