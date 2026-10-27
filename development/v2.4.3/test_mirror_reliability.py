#!/usr/bin/env python3
# coding: utf-8
"""Gitee CDN redirect and out-of-sync mirror regression tests."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "sort_reliable", ROOT / "development/v2.4.3/sort_update_core.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


class Response:
    def __init__(self, data, final_url):
        self.content = io.BytesIO(data)
        self.final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def geturl(self):
        return self.final_url

    def read(self, size=-1):
        return self.content.read(size)


class MirrorTests(unittest.TestCase):
    def test_gitee_signed_cdn_redirect(self):
        url = "https://gitee.com/HR-world/xingli/raw/main/sort/latest.json"
        final_url = ("https://raw.giteeusercontent.com/HR-world/xingli/raw/main/"
                     "sort/latest.json?metadata=opaque&signature=opaque")
        with patch.object(core.urllib.request, "urlopen",
                          return_value=Response(b"ok", final_url)):
            self.assertEqual(core._read_http(url, 100, 1), b"ok")

    def test_github_cannot_redirect_to_gitee_cdn(self):
        url = "https://raw.githubusercontent.com/haoranzheng/xingli/main/test.txt"
        final_url = "https://raw.giteeusercontent.com/haoranzheng/xingli/main/test.txt"
        with patch.object(core.urllib.request, "urlopen",
                          return_value=Response(b"bad", final_url)):
            with self.assertRaises(core.SortError):
                core._read_http(url, 100, 1)

    def test_gitee_cdn_must_preserve_requested_file_path(self):
        url = "https://gitee.com/HR-world/xingli/raw/main/sort/latest.json"
        final_url = "https://raw.giteeusercontent.com/HR-world/xingli/raw/main/update/latest.json"
        with patch.object(core.urllib.request, "urlopen",
                          return_value=Response(b"bad", final_url)):
            with self.assertRaises(core.SortError):
                core._read_http(url, 100, 1)

    def test_other_external_redirect_rejected(self):
        url = "https://gitee.com/HR-world/xingli/raw/main/sort/latest.json"
        with patch.object(core.urllib.request, "urlopen",
                          return_value=Response(b"bad", "https://example.com/foreign")):
            with self.assertRaises(core.SortError):
                core._read_http(url, 100, 1)

    def test_fresher_github_catalog_wins_if_gitee_lags(self):
        def catalog(version, timestamp):
            blob = b"+A\n"
            return json.dumps({
                "schema": 1, "product": core.PRODUCT, "pack_id": core.PACK_ID,
                "published_at": timestamp,
                "releases": [{
                    "id": "xingli-sort-test", "version": version,
                    "kind": "mods", "files": {"modlist.txt": {
                        "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
                        "mirrors": [{"url":
                          "https://raw.githubusercontent.com/haoranzheng/xingli/main/test.txt"}]
                    }}
                }]
            }).encode("utf-8")
        older = catalog("1.0.0", "2026-10-14T07:34:29+00:00")
        newer = catalog("1.0.1", "2026-10-27T04:00:00+00:00")
        def read_url(url, *_):
            return older if url.startswith("https://gitee.com/") else newer
        with patch.object(core, "_read_http", side_effect=read_url):
            self.assertEqual(core.fetch_releases()[0]["version"], "1.0.1")
        with patch.object(core, "_read_http", side_effect=lambda url,*_: older):
            self.assertEqual(core.fetch_releases()[0]["version"], "1.0.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
