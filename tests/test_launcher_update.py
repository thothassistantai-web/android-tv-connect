#!/usr/bin/env python3
"""Tests for launcher update logic (no network)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")
from android_tv_connect_launcher import manifest, paths, version
from android_tv_connect_launcher.version import (
    InstalledVersion,
    UpdateManifest,
    compare_versions,
    is_update_available,
    parse_version_code_from_body,
    parse_version_code_from_tag,
)


class VersionCompareTests(unittest.TestCase):
    def test_compare_versions(self) -> None:
        self.assertLess(compare_versions("1.0.0", "1.1.0"), 0)
        self.assertGreater(compare_versions("2.0.0", "1.9.9"), 0)
        self.assertEqual(compare_versions("1.2.3", "1.2.3"), 0)

    def test_is_update_available(self) -> None:
        installed = InstalledVersion(version="1.0.0", version_code=1)
        newer = UpdateManifest(
            version="1.1.0",
            version_code=2,
            bundle_url="https://example.com/bundle.tar.gz",
        )
        same = UpdateManifest(
            version="1.0.0",
            version_code=1,
            bundle_url="https://example.com/bundle.tar.gz",
        )
        self.assertTrue(is_update_available(installed, newer))
        self.assertFalse(is_update_available(installed, same))

    def test_parse_version_code_helpers(self) -> None:
        self.assertEqual(parse_version_code_from_body("Shipped versionCode: 42"), 42)
        self.assertEqual(parse_version_code_from_tag("v1.2.0+7"), 7)


class ManifestParseTests(unittest.TestCase):
    def test_parse_direct_manifest(self) -> None:
        payload = {
            "version": "1.2.0",
            "versionCode": 3,
            "bundleUrl": "https://example.com/android-tv-connect-1.2.0.tar.gz",
            "sha256": "abc",
            "releaseNotes": "Fixes",
        }
        parsed = manifest.parse_manifest_json(json.dumps(payload))
        assert parsed is not None
        self.assertEqual(parsed.version, "1.2.0")
        self.assertEqual(parsed.version_code, 3)
        self.assertEqual(parsed.bundle_url, payload["bundleUrl"])

    def test_parse_github_release_with_manifest_asset(self) -> None:
        release = {
            "tag_name": "v1.1.0",
            "body": "versionCode: 2",
            "assets": [
                {
                    "name": "update-manifest.json",
                    "browser_download_url": "https://example.com/update-manifest.json",
                }
            ],
        }

        manifest_json = json.dumps(
            {
                "version": "1.1.0",
                "versionCode": 2,
                "bundleUrl": "https://example.com/bundle.tar.gz",
            }
        )

        with patch.object(manifest, "_request_text", return_value=manifest_json):
            parsed = manifest.parse_github_release(json.dumps(release))

        assert parsed is not None
        self.assertEqual(parsed.version, "1.1.0")
        self.assertEqual(parsed.version_code, 2)


class SymlinkTests(unittest.TestCase):
    def test_set_current_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            versions = data_root / "versions"
            current = data_root / "current"
            versions.mkdir(parents=True)
            (versions / "1.0.0").mkdir()
            (versions / "1.1.0").mkdir()

            with patch.object(paths, "DATA_ROOT", data_root), patch.object(
                paths, "VERSIONS_DIR", versions
            ), patch.object(paths, "CURRENT_LINK", current):
                target = paths.set_current_symlink("1.1.0")

            self.assertTrue(current.is_symlink())
            self.assertEqual(current.resolve(), target.resolve())
            self.assertEqual(target.name, "1.1.0")


if __name__ == "__main__":
    unittest.main()
