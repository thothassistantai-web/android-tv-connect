"""Update orchestration for the launcher."""

from __future__ import annotations

from dataclasses import dataclass

from .installer import BundleInstallError, install_bundle
from .manifest import ManifestFetchError, fetch_update_manifest
from .paths import read_installed_version
from .settings import LauncherSettings, load_launcher_settings
from .version import InstalledVersion, UpdateManifest, is_mandatory, is_update_available


@dataclass(frozen=True)
class UpdateCheckResult:
    installed: InstalledVersion
    manifest: UpdateManifest | None
    update_available: bool
    mandatory: bool
    error: str | None = None


def check_for_update(
    *,
    settings: LauncherSettings | None = None,
    manifest_url: str | None = None,
) -> UpdateCheckResult:
    installed = read_installed_version()
    cfg = settings or load_launcher_settings()
    url = manifest_url or cfg.update_manifest_url

    try:
        manifest = fetch_update_manifest(url)
    except ManifestFetchError as exc:
        return UpdateCheckResult(
            installed=installed,
            manifest=None,
            update_available=False,
            mandatory=False,
            error=str(exc),
        )

    available = is_update_available(installed, manifest)
    mandatory = available and is_mandatory(installed, manifest)
    return UpdateCheckResult(
        installed=installed,
        manifest=manifest,
        update_available=available,
        mandatory=mandatory,
    )


def should_prompt(result: UpdateCheckResult, settings: LauncherSettings | None = None) -> bool:
    if not result.update_available or result.manifest is None:
        return False
    if result.mandatory:
        return True
    cfg = settings or load_launcher_settings()
    return result.manifest.version_code > cfg.dismissed_update_version_code


def apply_update(manifest: UpdateManifest, *, force: bool = False) -> None:
    install_bundle(manifest, force=force)
