#!/usr/bin/env python3
# coding: utf-8
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("stage",ROOT/"development/v2.4.2/stage_inbox_sort.py")
stage=importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)

class Cases(unittest.TestCase):
    def _prepare(self,home):
        root=Path(home)
        inbox=root/"sort/inbox/20261014"
        inbox.mkdir(parents=True)
        (inbox/"modlist(2).txt").write_bytes(b"+A\r\n-B\r\n")
        (inbox/"plugins.txt").write_bytes(b"*A.esm\r\n")
        (inbox/"loadorder.txt").write_bytes(b"A.esm\r\n")
        return root,inbox

    def test_missing_trio_does_not_stage_or_notify(self):
        with tempfile.TemporaryDirectory() as d:
            root,inbox=self._prepare(d)
            self.assertFalse(stage.stage_one(root,inbox))
            self.assertFalse((root/"sort/releases").exists())

    def test_four_text_files_stage_without_zip(self):
        with tempfile.TemporaryDirectory() as d:
            root,inbox=self._prepare(d)
            (inbox/"lockedorder.txt").write_bytes(b"# generated\r\n")
            self.assertTrue(stage.stage_one(root,inbox))
            for sub,filename,expected in [
                ("xingli-modlist-20261014-r01","modlist.txt",b"+A\r\n-B\r\n"),
                ("xingli-plugins-20261014-r01","plugins.txt",b"*A.esm\r\n"),
                ("xingli-plugins-20261014-r01","loadorder.txt",b"A.esm\r\n"),
                ("xingli-plugins-20261014-r01","lockedorder.txt",b"# generated\r\n"),
            ]:
                dest=root/"sort/releases"/sub/"v1.0.0"/filename
                self.assertEqual(dest.read_bytes(),expected)
            self.assertFalse(any((root/"sort/releases").rglob("*.zip")))
            self.assertTrue(stage.stage_one(root,inbox))

    def test_immutable_version_conflict_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root,inbox=self._prepare(d)
            (inbox/"lockedorder.txt").write_bytes(b"# generated\n")
            self.assertTrue(stage.stage_one(root,inbox))
            (inbox/"plugins.txt").write_bytes(b"*Different.esp\n")
            with self.assertRaises(RuntimeError):
                stage.stage_one(root,inbox)

    def test_github_direct_manifest_generation(self):
        with tempfile.TemporaryDirectory() as d:
            root,inbox=self._prepare(d)
            (inbox/"lockedorder.txt").write_bytes(b"# generated\n")
            (root/"sort/latest.json").write_text(json.dumps({
                "schema":1,"product":"xingli_sort_updates","pack_id":"xingli-skyrim",
                "channel":"stable","releases":[]}),encoding="utf-8")
            self.assertTrue(stage.stage_one(root,inbox))
            data,records=stage.publisher.generate(root)
            self.assertEqual(len(records),2)
            self.assertEqual({x["kind"] for x in records},{"mods","plugins"})
            self.assertTrue(all("files" in x and "package" not in x for x in records))

if __name__=="__main__":
    unittest.main(verbosity=2)
