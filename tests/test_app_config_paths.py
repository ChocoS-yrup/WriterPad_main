"""Settings must not depend on a shortcut/automation tool's working directory."""
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtGui import QFont


SOURCE = Path(__file__).resolve().parents[1] / "app_config.py"


class AppConfigPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.install = Path(self.temp.name, "installed")
        self.install.mkdir()
        self.foreign = Path(self.temp.name, "launcher")
        self.foreign.mkdir()
        # A directory with this name makes accidental relative writes fail
        # without depending on Windows ACLs or administrator privileges.
        (self.foreign / "config.json").mkdir()
        previous_cwd = os.getcwd()
        os.chdir(self.foreign)
        self.addCleanup(os.chdir, previous_cwd)

    def load(self, override=""):
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", str(self.install / "WriterPad.exe")
        ), patch.dict(os.environ, {"ANTIGRAVITY_ROOT_DIR": override}):
            return runpy.run_path(str(SOURCE))

    def test_config_uses_install_directory_and_preserves_existing_values(self):
        target = self.install / "config.json"
        target.write_text(json.dumps({"last_project": "synthetic", "custom": 123}), encoding="utf-8")
        settings = self.load()
        settings["save_config"]("writing_last_active_editor", "right")
        saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(saved["writing_last_active_editor"], "right")
        self.assertEqual(saved["last_project"], "synthetic")
        self.assertEqual(saved["custom"], 123)
        self.assertTrue((self.foreign / "config.json").is_dir())

    def test_missing_config_is_created_beside_executable(self):
        settings = self.load()
        self.assertEqual(settings["get_config"](), settings["DEFAULT_CONFIG"])
        self.assertTrue((self.install / "config.json").is_file())

    def test_font_settings_use_same_install_directory(self):
        settings = self.load()
        settings["save_font_to_json"](QFont("Arial", 17))
        saved = json.loads((self.install / "fonts.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["font_size"], 17)
        self.assertFalse((self.foreign / "fonts.json").exists())

    def test_runtime_root_override_is_honored(self):
        profile = Path(self.temp.name, "profile")
        profile.mkdir()
        settings = self.load(str(profile))
        settings["save_config"]("writing_last_active_editor", "left")
        self.assertTrue((profile / "config.json").is_file())
        self.assertFalse((self.install / "config.json").exists())


if __name__ == "__main__":
    unittest.main()
