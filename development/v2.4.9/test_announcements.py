# coding: utf-8
"""Smoke and safety regression for Xingli remote shader announcement push."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
CODE = Path(__file__).with_name("announcement_core.py")
SPEC = importlib.util.spec_from_file_location("xingli_announcement_core_test", CODE)
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


class Organizer:
    def __init__(self, root):
        self.root = root

    def pluginDataPath(self):
        return str(self.root)


class Tests(unittest.TestCase):
    def setUp(self):
        self.live = json.loads((ROOT / "announcements/latest.json").read_text(encoding="utf-8"))
        self.items = core.validate_manifest(self.live)

    def test_live_shader_announcement_exact_user_links(self):
        self.assertEqual(self.live["product"], core.PRODUCT)
        self.assertEqual(len(self.items), 1)
        item = self.items[0]
        self.assertEqual(item["title"], "星黎个人定制着色器已更新")
        self.assertEqual(item["category"], "shader")
        self.assertEqual([x["url"] for x in item["links"]], [
            "https://pan.quark.cn/s/ff5ece3e4cb7",
            "https://pan.baidu.com/s/18Vtwns7Kj8JpWU_vELsbxw?pwd=p58j",
        ])
        self.assertEqual(item["links"][1]["code"], "p58j")
        self.assertNotIn("DLSS 5", item["message"])

    def test_block_redirector_and_untrusted_pages(self):
        for url in (
            "http://pan.quark.cn/s/ff5ece3e4cb7",
            "https://pan.quark.cn.evil.example/s/ff5ece3e4cb7",
            "https://example.org/redirect",
            "file:///C:/Windows",
            "https://pan.baidu.com@evil.example/s/abc123456",
            "https://pan.baidu.com/s/abc123456#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(core.AnnouncementError):
                core._validate_download_page(url)

    def test_revision_and_duplicate_id_validation(self):
        bad = json.loads(json.dumps(self.live))
        bad["announcements"][0]["revision"] = 0
        with self.assertRaises(core.AnnouncementError):
            core.validate_manifest(bad)
        bad = json.loads(json.dumps(self.live))
        bad["announcements"].append(dict(bad["announcements"][0]))
        with self.assertRaises(core.AnnouncementError):
            core.validate_manifest(bad)

    def test_unseen_and_receipt_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            organizer = Organizer(Path(tmp) / "plugin-data")
            path = core.receipt_path(organizer)
            item = self.items[0]
            self.assertEqual(core.unseen(self.items, core.read_receipts(path)), self.items)
            core.mark_seen(path, item)
            self.assertEqual(core.unseen(self.items, core.read_receipts(path)), [])
            updated = dict(item, revision=item["revision"] + 1)
            self.assertEqual(core.unseen([updated], core.read_receipts(path)), [updated])
            core.mark_seen(path, updated)
            core.mark_seen(path, item)  # older announcement must not downgrade read receipt
            self.assertEqual(core.read_receipts(path)[item["id"]], updated["revision"])
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_corrupt_receipt_is_treated_as_unread(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "announcements.json"
            path.write_text("bad-json", encoding="utf-8")
            self.assertEqual(core.read_receipts(path), {})
            self.assertEqual(core.unseen(self.items, core.read_receipts(path)), self.items)

    def test_mirror_merging_prefers_fresher_revision_even_if_gitee_lags(self):
        stale = json.loads(json.dumps(self.live))
        fresh = json.loads(json.dumps(self.live))
        fresh["announcements"][0]["revision"] = 2
        fresh["announcements"][0]["message"] += " 新增说明"
        with mock.patch.object(core, "_read_http", side_effect=[
            json.dumps(stale).encode("utf-8"),
            json.dumps(fresh).encode("utf-8"),
        ]):
            records = core.fetch_announcements()
        self.assertEqual(records[0]["revision"], 2)
        self.assertIn("新增说明", records[0]["message"])

    def test_github_fallback_works_if_gitee_unavailable(self):
        with mock.patch.object(core, "_read_http", side_effect=[
            RuntimeError("gitee unavailable"), json.dumps(self.live).encode("utf-8")
        ]):
            records = core.fetch_announcements()
        self.assertEqual(records, self.items)

    def test_all_mirrors_unavailable_fail_without_marking_seen(self):
        with mock.patch.object(core, "_read_http", side_effect=RuntimeError("network down")):
            with self.assertRaises(core.AnnouncementError):
                core.fetch_announcements()


if __name__ == "__main__":
    unittest.main(verbosity=2)
