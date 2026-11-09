# coding: utf-8
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).with_name("publish_profiles.py")
SPEC = importlib.util.spec_from_file_location("publish_profiles_test", SCRIPT)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


def create_source(root, version="1.0.0", modlist="+Unofficial Patch\n"):
    folder = Path(root) / "profiles" / "releases" / "xingli-default"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "profile.json").write_text(json.dumps({
        "id": "xingli-default",
        "name": "星黎默认档案",
        "version": version,
        "description": "公开的 MO2 Profile 配置",
        "changelog": "测试发布",
    }, ensure_ascii=False), encoding="utf-8")
    (folder / "modlist.txt").write_text(modlist, encoding="utf-8")
    (folder / "plugins.txt").write_text("*Unofficial Patch.esp\n", encoding="utf-8")
    (folder / "loadorder.txt").write_text("Unofficial Patch.esp\n", encoding="utf-8")
    (folder / "lockedorder.txt").write_text("", encoding="utf-8")
    (folder / "Skyrim.ini").write_text("[General]\n", encoding="utf-8")
    return folder


class ProfilePublisherTests(unittest.TestCase):
    def test_empty_catalog_is_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = publisher.build(temp)
            self.assertEqual(manifest["profiles"], [])
            self.assertEqual(manifest["product"], "xingli_profile_downloads")

    def test_profile_folder_builds_immutable_zip_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            create_source(temp)
            manifest = publisher.build(temp)
            self.assertEqual(len(manifest["profiles"]), 1)
            item = manifest["profiles"][0]
            self.assertEqual(item["version"], "1.0.0")
            package = Path(temp) / "profiles" / "packages" / item["package"]["filename"]
            blob = package.read_bytes()
            self.assertEqual(len(blob), item["package"]["size"])
            self.assertEqual(hashlib.sha256(blob).hexdigest(), item["package"]["sha256"])
            with zipfile.ZipFile(package) as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("modlist.txt", archive.namelist())
                self.assertNotIn("profile.json", archive.namelist())
            self.assertIn("1.0.0", (Path(temp) / "profiles" / "latest.json").read_text(encoding="utf-8"))

    def test_same_id_and_version_cannot_silently_overwrite_package(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = create_source(temp)
            publisher.build(temp)
            (folder / "modlist.txt").write_text("+Changed Mod\n", encoding="utf-8")
            with self.assertRaisesRegex(publisher.PublishError, "不可覆盖"):
                publisher.build(temp)

    def test_new_version_creates_new_package_without_date_based_versioning(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = create_source(temp)
            publisher.build(temp)
            (folder / "profile.json").write_text(json.dumps({
                "id": "xingli-default",
                "name": "星黎默认档案",
                "version": "1.0.1",
            }, ensure_ascii=False), encoding="utf-8")
            (folder / "modlist.txt").write_text("+New Mod\n", encoding="utf-8")
            manifest = publisher.build(temp)
            self.assertEqual(manifest["profiles"][0]["version"], "1.0.1")
            packages = list((Path(temp) / "profiles" / "packages").glob("*.zip"))
            self.assertEqual({item.name for item in packages}, {
                "xingli-default-v1.0.0.zip", "xingli-default-v1.0.1.zip"
            })

    def test_invalid_version_and_missing_modlist_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = create_source(temp, version="2026-11-09")
            with self.assertRaises(publisher.PublishError):
                publisher.build(temp)
        with tempfile.TemporaryDirectory() as temp:
            folder = create_source(temp)
            (folder / "modlist.txt").unlink()
            with self.assertRaisesRegex(publisher.PublishError, "modlist.txt"):
                publisher.build(temp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
