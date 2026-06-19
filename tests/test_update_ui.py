#!/usr/bin/env python3
"""Tests for in-app update UI launcher resolution."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")
from android_tv_connect import update_ui


class UpdateUiLauncherTests(unittest.TestCase):
    def test_prefers_app_home_checkout_over_default_launcher_copy(self) -> None:
        checkout = Path("/tmp/atv-dev-checkout").resolve()
        default_launcher = (
            Path.home() / ".local" / "share" / "android-tv-connect" / "launcher"
        )

        with patch.object(
            update_ui,
            "_resolve_launcher_root",
            return_value=checkout,
        ), patch(
            "android_tv_connect_launcher.paths.resolve_data_root",
            return_value=checkout,
        ):
            cmd, env = update_ui._launcher_command("--check-updates")

        self.assertEqual(cmd[0], sys.executable)
        self.assertIn("android_tv_connect_launcher", cmd)
        self.assertEqual(env["PYTHONPATH"], str(checkout))
        self.assertEqual(env["ATV_CONNECT_HOME"], str(checkout))
        self.assertNotEqual(env["PYTHONPATH"], str(default_launcher))

    def test_resolve_launcher_root_uses_launcher_dir(self) -> None:
        checkout = Path(__file__).resolve().parent.parent
        with patch.dict(
            os.environ,
            {"ATV_CONNECT_HOME": str(checkout)},
            clear=False,
        ):
            root = update_ui._resolve_launcher_root()

        self.assertEqual(root, checkout)


if __name__ == "__main__":
    unittest.main()
