"""Version parsing and comparison."""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION_CODE_BODY_RE = re.compile(r"versionCode\s*[:=]\s*(\d+)", re.IGNORECASE)
_VERSION_CODE_TAG_RE = re.compile(r"(?:\+|\()(\d+)\)?$")
_SEMVER_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?$")


@dataclass(frozen=True)
class InstalledVersion:
    version: str
    version_code: int


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    version_code: int
    bundle_url: str
    sha256: str = ""
    mandatory: bool = False
    release_notes: str = ""
    min_version_code: int | None = None


def parse_semver(version: str) -> tuple[int, int, int]:
    text = version.strip().removeprefix("v")
    match = _SEMVER_RE.match(text)
    if not match:
        return (0, 0, 0)
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    return (major, minor, patch)


def compare_versions(left: str, right: str) -> int:
    """Return negative if left < right, zero if equal, positive if left > right."""
    a = parse_semver(left)
    b = parse_semver(right)
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def is_update_available(installed: InstalledVersion, manifest: UpdateManifest) -> bool:
    if manifest.version_code > installed.version_code:
        return True
    if manifest.version_code < installed.version_code:
        return False
    return compare_versions(manifest.version, installed.version) > 0


def is_mandatory(installed: InstalledVersion, manifest: UpdateManifest) -> bool:
    if manifest.mandatory:
        return True
    if manifest.min_version_code is not None and installed.version_code < manifest.min_version_code:
        return True
    return False


def parse_version_code_from_body(body: str | None) -> int | None:
    if not body:
        return None
    match = _VERSION_CODE_BODY_RE.search(body)
    if not match:
        return None
    return int(match.group(1))


def parse_version_code_from_tag(tag: str) -> int | None:
    match = _VERSION_CODE_TAG_RE.search(tag.strip())
    if not match:
        return None
    return int(match.group(1))
