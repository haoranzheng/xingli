#!/usr/bin/env python3
# coding: utf-8
"""Regression tests for the Git CRLF-to-LF sorting manifest failure."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "staged_sort_checker", ROOT / "development/v2.4.2/check_staged_sort_manifest.py")
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


class StagedCatalogTests(unittest.TestCase):
    def test_checked_out_catalog_matches_committed_release_bytes(self):
        self.assertEqual(checker.verify_staged_catalog(ROOT), 4)

    def test_normalized_staged_bytes_reject_worktree_crlf_hash(self):
        original = b"+Example\r\n"
        committed = b"+Example\n"
        entry = {
            "id": "xingli-mods-fixture", "version": "1.0.0", "kind": "mods",
            "files": {"modlist.txt": {
                "size": len(original),
                "sha256": hashlib.sha256(original).hexdigest(),
                "mirrors": [{"url": "https://raw.githubusercontent.com/example/modlist.txt"}]
            }}
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "sort/latest.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "schema": 1, "product": checker.core.PRODUCT,
                "pack_id": checker.core.PACK_ID, "releases": [entry]
            }), encoding="utf-8")
            with patch.object(checker.subprocess, "run",
                              return_value=SimpleNamespace(stdout=committed)):
                with self.assertRaisesRegex(RuntimeError, "Catalog mismatch after git add"):
                    checker.verify_staged_catalog(root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
