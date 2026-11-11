# coding: utf-8
"""Regression for self-updater's exact packaged version.ini reader."""
import ast
import configparser
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "update/releases/xingli_Little_assistant_v2.5.0_update.zip"

class PackageVersionTests(unittest.TestCase):
    def test_manifest_hash_and_actual_version_ini_reader(self):
        manifest = json.loads((ROOT / "update/latest.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(manifest["version"], "2.5.0")
        path = PACKAGE
        blob = path.read_bytes()
        self.assertEqual(len(blob), manifest["package"]["size"])
        self.assertEqual(hashlib.sha256(blob).hexdigest(), manifest["package"]["sha256"])
        with zipfile.ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            ini = archive.read("xingli_Little_assistant/version.ini")
            self.assertEqual(ini, b"[Version]\nlocal = 2.5.0\n")
            source = archive.read("xingli_Little_assistant/auto_updater.py").decode("utf-8-sig")
            tree = ast.parse(source)
            matches = [node for node in tree.body
                       if isinstance(node, ast.FunctionDef) and node.name == "_read_staged_version"]
            self.assertEqual(len(matches), 1)
            namespace = {"configparser": configparser, "os": os}
            exec(compile(ast.Module(body=matches, type_ignores=[]),
                         "auto_updater.py", "exec"), namespace)
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                (folder / "version.ini").write_bytes(ini)
                self.assertEqual(namespace["_read_staged_version"](str(folder)), "2.5.0")

if __name__ == "__main__":
    unittest.main(verbosity=2)
