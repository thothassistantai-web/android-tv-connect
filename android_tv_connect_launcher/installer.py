"""Download, verify, and install application bundles."""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .constants import USER_AGENT
from .paths import ensure_layout, set_current_symlink, version_dir, write_installed_meta
from .version import UpdateManifest


class BundleInstallError(RuntimeError):
    pass


def _download_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(destination)
        return
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(destination)
        return
    raise BundleInstallError(f"Unsupported bundle format: {archive.name}")


def _normalize_extracted_root(extracted: Path) -> Path:
    if (extracted / "android_tv_connect").is_dir():
        return extracted
    children = [child for child in extracted.iterdir() if child.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extracted


def install_bundle(manifest: UpdateManifest, *, force: bool = False) -> Path:
    ensure_layout()
    target = version_dir(manifest.version)
    if target.is_dir() and not force:
        set_current_symlink(manifest.version)
        write_installed_meta(manifest.version, manifest.version_code)
        return target

    if target.is_dir():
        shutil.rmtree(target)

    with tempfile.TemporaryDirectory(prefix="atv-connect-update-") as tmp:
        tmp_path = Path(tmp)
        archive_path = tmp_path / "bundle.archive"
        _download_file(manifest.bundle_url, archive_path)

        if manifest.sha256:
            actual = _sha256_file(archive_path)
            expected = manifest.sha256.lower()
            if actual != expected:
                raise BundleInstallError(
                    f"Checksum mismatch: expected {expected}, got {actual}"
                )

        extract_dir = tmp_path / "extracted"
        _extract_archive(archive_path, extract_dir)
        bundle_root = _normalize_extracted_root(extract_dir)
        if not (bundle_root / "android_tv_connect").is_dir():
            raise BundleInstallError("Bundle missing android_tv_connect package")

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(bundle_root), str(target))

    set_current_symlink(manifest.version)
    write_installed_meta(manifest.version, manifest.version_code)
    return target
