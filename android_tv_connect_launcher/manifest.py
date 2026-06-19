"""Fetch and parse update manifests (direct JSON or GitHub Releases API)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .constants import (
    DEFAULT_GITHUB_RAW_MANIFEST_URL,
    DEFAULT_UPDATE_MANIFEST_URL,
    MANIFEST_ASSET_NAME,
    USER_AGENT,
)
from .version import (
    UpdateManifest,
    parse_version_code_from_body,
    parse_version_code_from_tag,
)

_GITHUB_RELEASES_RE = re.compile(
    r"https?://api\.github\.com/repos/[^/]+/[^/]+/releases",
    re.IGNORECASE,
)
_GITHUB_RELEASES_API_RE = re.compile(
    r"^https?://api\.github\.com/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases(?:/latest|/tags/(?P<tag>[^/?#]+))?\s*$",
    re.IGNORECASE,
)


class ManifestFetchError(RuntimeError):
    pass


def resolve_github_token(explicit: str | None = None) -> str:
    token = (explicit or "").strip()
    if token:
        return token
    return os.environ.get("GITHUB_TOKEN", "").strip()


def _build_request_headers(*, accept: str, github_token: str = "") -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    return headers


def _request_text(
    url: str,
    *,
    accept: str = "application/json",
    github_token: str = "",
) -> str:
    request = urllib.request.Request(
        url,
        headers=_build_request_headers(accept=accept, github_token=github_token),
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        message = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
            payload = json.loads(body)
            if isinstance(payload, dict):
                message = str(payload.get("message", "")).strip()
                if message:
                    detail = f": {message}"
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        if exc.code == 403 and (
            "rate limit" in message.lower() or not message
        ):
            hint = (
                "GitHub API rate limit or access denied. "
                "Set GITHUB_TOKEN (or updates.github_token in config) and retry, "
                "or wait and try again."
            )
            raise ManifestFetchError(f"{hint}{detail}") from exc
        raise ManifestFetchError(f"HTTP {exc.code} for {url}{detail}") from exc
    except urllib.error.URLError as exc:
        raise ManifestFetchError(f"Network error for {url}: {exc.reason}") from exc


def is_github_releases_url(url: str) -> bool:
    return bool(_GITHUB_RELEASES_RE.search(url.strip()))


def github_raw_manifest_fallback_url(url: str) -> str | None:
    """Map a GitHub Releases API URL to a static update-manifest.json download URL."""
    match = _GITHUB_RELEASES_API_RE.match(url.strip())
    if match is None:
        return None

    owner = match.group("owner")
    repo = match.group("repo")
    tag = match.group("tag")
    if tag:
        return (
            f"https://github.com/{owner}/{repo}/releases/download/"
            f"{tag}/{MANIFEST_ASSET_NAME}"
        )
    return (
        f"https://github.com/{owner}/{repo}/releases/latest/download/"
        f"{MANIFEST_ASSET_NAME}"
    )


def _coerce_manifest(raw: dict[str, Any]) -> UpdateManifest | None:
    version = str(
        raw.get("version")
        or raw.get("versionName")
        or raw.get("tag_name", "")
    ).strip()
    if not version:
        return None

    version_code = raw.get("versionCode")
    if version_code is None:
        version_code = raw.get("version_code", 0)
    try:
        code = int(version_code)
    except (TypeError, ValueError):
        code = 0

    bundle_url = str(
        raw.get("bundleUrl")
        or raw.get("bundle_url")
        or raw.get("apkUrl")
        or raw.get("apk_url")
        or ""
    ).strip()
    if not bundle_url:
        return None

    sha256 = str(raw.get("sha256") or raw.get("sha256sum") or "").strip().lower()
    release_notes = str(raw.get("releaseNotes") or raw.get("release_notes") or "").strip()
    mandatory = bool(raw.get("mandatory", False))
    min_code = raw.get("minVersionCode", raw.get("min_version_code"))
    min_version_code = int(min_code) if min_code is not None else None

    return UpdateManifest(
        version=version.removeprefix("v"),
        version_code=code,
        bundle_url=bundle_url,
        sha256=sha256,
        mandatory=mandatory,
        release_notes=release_notes,
        min_version_code=min_version_code,
    )


def parse_manifest_json(text: str) -> UpdateManifest | None:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestFetchError("Invalid manifest JSON") from exc
    if not isinstance(raw, dict):
        return None
    return _coerce_manifest(raw)


def parse_github_release(text: str, *, github_token: str = "") -> UpdateManifest | None:
    try:
        release = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestFetchError("Invalid GitHub release JSON") from exc

    assets = release.get("assets") or []
    manifest_asset = next(
        (
            asset
            for asset in assets
            if str(asset.get("name", "")).lower() == MANIFEST_ASSET_NAME.lower()
        ),
        None,
    )
    if manifest_asset is not None:
        manifest_url = str(manifest_asset.get("browser_download_url", "")).strip()
        if manifest_url:
            manifest_text = _request_text(
                manifest_url,
                accept="application/json",
                github_token=github_token,
            )
            parsed = parse_manifest_json(manifest_text)
            if parsed is not None:
                return parsed

    bundle_asset = next(
        (
            asset
            for asset in assets
            if str(asset.get("name", "")).endswith((".tar.gz", ".tgz", ".zip"))
        ),
        None,
    )
    if bundle_asset is None:
        return None

    tag_name = str(release.get("tag_name", "")).strip()
    version = tag_name.removeprefix("v")
    if not version:
        version = str(release.get("name", "")).strip().removeprefix("v")
    body = release.get("body")
    version_code = (
        parse_version_code_from_body(body if isinstance(body, str) else None)
        or parse_version_code_from_tag(tag_name)
        or 0
    )
    if version_code <= 0:
        return None

    return UpdateManifest(
        version=version,
        version_code=version_code,
        bundle_url=str(bundle_asset.get("browser_download_url", "")).strip(),
        release_notes=str(body or "").strip(),
    )


def _fetch_manifest_from_url(url: str, *, github_token: str = "") -> UpdateManifest:
    text = _request_text(url, github_token=github_token)
    if is_github_releases_url(url):
        manifest = parse_github_release(text, github_token=github_token)
    else:
        manifest = parse_manifest_json(text)

    if manifest is None:
        raise ManifestFetchError("Could not parse update manifest")
    return manifest


def fetch_update_manifest(
    url: str | None = None,
    *,
    github_token: str | None = None,
) -> UpdateManifest:
    manifest_url = (url or DEFAULT_UPDATE_MANIFEST_URL).strip()
    if not manifest_url:
        raise ManifestFetchError("Update manifest URL is empty")

    token = resolve_github_token(github_token)

    try:
        return _fetch_manifest_from_url(manifest_url, github_token=token)
    except ManifestFetchError as primary_exc:
        fallback_url = github_raw_manifest_fallback_url(manifest_url)
        if fallback_url is None:
            if (
                manifest_url == DEFAULT_UPDATE_MANIFEST_URL
                and DEFAULT_GITHUB_RAW_MANIFEST_URL != manifest_url
            ):
                fallback_url = DEFAULT_GITHUB_RAW_MANIFEST_URL
            else:
                raise primary_exc

        try:
            return _fetch_manifest_from_url(fallback_url, github_token=token)
        except ManifestFetchError as fallback_exc:
            raise ManifestFetchError(
                f"Update check failed ({primary_exc}). "
                f"Fallback manifest also failed ({fallback_exc})."
            ) from fallback_exc
