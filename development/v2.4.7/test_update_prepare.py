# coding: utf-8
"""Regression test for the exact self-updater failure reported in v2.4.6."""
import ast
import configparser
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "update/releases/xingli_Little_assistant_v2.4.6_update.zip"
NEW = ROOT / "update/releases/xingli_Little_assistant_v2.4.7_update.zip"
INI = "xingli_Little_assistant/version.ini"
UPDATER = "xingli_Little_assistant/auto_updater.py"
CONTROLLER = "xingli_Little_assistant/consolidation_controller.py"
CHANGELOG = "xingli_Little_assistant/CHANGELOG_v2.4.6.md"
NEW_LOG = "xingli_Little_assistant/CHANGELOG_v2.4.7.md"


def get_actual_staged_version_function(archive):
    """Compile the ACTUAL _read_staged_version implementation, not a copy."""
    source = archive.read(UPDATER).decode("utf-8-sig")
    tree = ast.parse(source)
    found = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == "_read_staged_version"]
    if len(found) != 1:
        raise AssertionError("Cannot identify packaged updater's parser")
    code = compile(ast.Module(body=found, type_ignores=[]), "<actual-auto-updater>", "exec")
    globals_ = {"configparser": configparser, "os": os}
    exec(code, globals_)
    return globals_["_read_staged_version"]


class UpdateHotfixTests(unittest.TestCase):
    def test_reproduce_246_failure_and_verify_247_passes_same_actual_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            location = Path(tmp) / "xingli_Little_assistant"
            location.mkdir()
            with zipfile.ZipFile(OLD) as original:
                old_func = get_actual_staged_version_function(original)
                old_ini = original.read(INI)
                self.assertEqual(old_ini, b"[Version]\\nlocal = 2.4.6\\n")
                (location / "version.ini").write_bytes(old_ini)
                with self.assertRaisesRegex(RuntimeError, "更新包版本信息无效"):
                    old_func(str(location))
            with zipfile.ZipFile(NEW) as fixed:
                new_func = get_actual_staged_version_function(fixed)
                new_ini = fixed.read(INI)
                self.assertEqual(new_ini, b"[Version]\nlocal = 2.4.7\n")
                (location / "version.ini").write_bytes(new_ini)
                self.assertEqual(new_func(str(location)), "2.4.7")

    def test_package_matches_remote_manifest_version_hash_and_size(self):
        manifest = json.loads((ROOT / "update/latest.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(manifest["version"], "2.4.7")
        self.assertEqual(manifest["package"]["filename"], NEW.name)
        blob = NEW.read_bytes()
        self.assertEqual(len(blob), manifest["package"]["size"])
        self.assertEqual(hashlib.sha256(blob).hexdigest(),
                         manifest["package"]["sha256"])
        with zipfile.ZipFile(NEW) as package:
            self.assertIsNone(package.testzip())
            parser = configparser.ConfigParser()
            with tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / "version.ini"
                target.write_bytes(package.read(INI))
                self.assertEqual(parser.read(str(target), encoding="utf-8"), [str(target)])
            self.assertEqual(parser.get("Version", "local"), manifest["version"])

    def test_exact_diff_and_preserved_modpack_functionality(self):
        with zipfile.ZipFile(OLD) as original, zipfile.ZipFile(NEW) as fixed:
            old = {item.filename: original.read(item) for item in original.infolist()
                   if not item.is_dir()}
            new = {item.filename: fixed.read(item) for item in fixed.infolist()
                   if not item.is_dir()}
        self.assertEqual(set(old), set(new) - {NEW_LOG})
        modified = {name for name in old if old[name] != new[name]}
        self.assertEqual(modified, {INI, CONTROLLER, CHANGELOG})
        self.assertEqual(new["xingli_Little_assistant/modpack_release.json"],
                         old["xingli_Little_assistant/modpack_release.json"])
        self.assertEqual(new["xingli_Little_assistant/modpack_confirmation.py"],
                         old["xingli_Little_assistant/modpack_confirmation.py"])
        self.assertEqual(new[UPDATER], old[UPDATER])
        self.assertNotIn(b"\\n", new[INI])
        self.assertIn(b"\n", new[CHANGELOG])
        self.assertNotIn(b"\\n", new[CHANGELOG])


if __name__ == "__main__":
    unittest.main(verbosity=2)
