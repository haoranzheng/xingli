# coding: utf-8
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest import mock

MODULE_PATH = Path(__file__).with_name("profile_download_core.py")
SPEC = importlib.util.spec_from_file_location("profile_download_core_test", MODULE_PATH)
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


def make_archive(files=None):
    files = files or {
        "modlist.txt": b"+Unofficial Patch\n",
        "plugins.txt": b"*Unofficial Patch.esp\n",
        "loadorder.txt": b"Unofficial Patch.esp\n",
        "lockedorder.txt": b"",
        "Skyrim.ini": b"[General]\n",
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return out.getvalue()


def make_record(blob, ident="xingli-profile", name="星黎测试档案", version="1.0.0"):
    return {
        "id": ident,
        "name": name,
        "version": version,
        "description": "测试档案",
        "package": {
            "size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "mirrors": [{
                "name": "Gitee",
                "url": "https://gitee.com/HR-world/xingli/raw/main/profiles/packages/test.zip",
            }],
        },
    }


class FakeOrganizer:
    def __init__(self, active, plugin_data):
        self.active = str(active)
        self.plugin_data = str(plugin_data)

    def profilePath(self):
        return self.active

    def pluginDataPath(self):
        return self.plugin_data


class ProfileDownloadTests(unittest.TestCase):
    def test_manifest_accepts_semver_without_any_date(self):
        blob = make_archive()
        record = make_record(blob)
        catalog = {
            "schema": 1,
            "product": core.PRODUCT,
            "profiles": [record],
        }
        validated = core.validate_manifest(catalog)
        self.assertEqual(validated[0]["version"], "1.0.0")
        self.assertEqual(validated[0]["id"], "xingli-profile")

    def test_manifest_rejects_untrusted_download_host(self):
        blob = make_archive()
        record = make_record(blob)
        record["package"]["mirrors"][0]["url"] = "https://example.com/profile.zip"
        with self.assertRaises(core.ProfileError):
            core.validate_manifest({
                "schema": 1, "product": core.PRODUCT, "profiles": [record]
            })

    def test_manifest_fetch_uses_catalog_and_no_date_parser(self):
        blob = make_archive()
        record = make_record(blob)
        catalog = json.dumps({
            "schema": 1, "product": core.PRODUCT, "profiles": [record]
        }).encode("utf-8")
        with mock.patch.object(core, "_read_http", return_value=catalog):
            result = core.fetch_profiles()
        self.assertEqual(result[0]["id"], record["id"])

    def test_download_checks_size_and_sha256(self):
        blob = make_archive()
        record = make_record(blob)
        with mock.patch.object(core, "_read_http", return_value=blob):
            self.assertEqual(core.download_package(record), blob)
        broken = dict(record)
        broken["package"] = dict(record["package"], sha256="0" * 64)
        with mock.patch.object(core, "_read_http", return_value=blob):
            with self.assertRaisesRegex(core.ProfileError, "SHA256"):
                core.download_package(broken)

    def test_zip_slip_and_duplicate_windows_names_are_rejected(self):
        bad = make_archive({"../outside.txt": b"bad", "modlist.txt": b"+A\n"})
        with self.assertRaises(core.ProfileError):
            core.validate_profile_archive(bad)
        duplicate = make_archive({
            "modlist.txt": b"+A\n",
            "MODLIST.TXT": b"+B\n",
        })
        with self.assertRaises(core.ProfileError):
            core.validate_profile_archive(duplicate)

    def test_missing_modlist_is_rejected(self):
        with self.assertRaisesRegex(core.ProfileError, "modlist.txt"):
            core.validate_profile_archive(make_archive({"plugins.txt": b"*A.esp\n"}))

    def test_install_creates_new_profile_without_touching_active(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "profiles"
            active = root / "Current"
            active.mkdir(parents=True)
            (active / "modlist.txt").write_text("+Current Mod\n", encoding="utf-8")
            organizer = FakeOrganizer(active, Path(temp) / "plugin-data")
            blob = make_archive()
            record = make_record(blob, name="星黎 4.0 默认档案")
            result = core.install_profile_archive(organizer, record, blob)
            target = Path(result["path"])
            self.assertEqual(target.parent, root)
            self.assertTrue((target / "modlist.txt").is_file())
            self.assertEqual((target / "Skyrim.ini").read_bytes(), b"[General]\n")
            self.assertEqual((active / "modlist.txt").read_text(encoding="utf-8"), "+Current Mod\n")
            self.assertEqual(result["files"], 5)

    def test_same_profile_download_never_overwrites_existing_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "profiles"
            active = root / "Current"
            active.mkdir(parents=True)
            (active / "modlist.txt").write_text("+Current\n", encoding="utf-8")
            existing = root / "星黎默认档案"
            existing.mkdir()
            (existing / "modlist.txt").write_text("+User Changes\n", encoding="utf-8")
            organizer = FakeOrganizer(active, Path(temp) / "plugin-data")
            record = make_record(make_archive(), name="星黎默认档案")
            first = core.install_profile_archive(organizer, record, make_archive())
            second = core.install_profile_archive(organizer, record, make_archive())
            self.assertEqual(Path(first["path"]).name, "星黎默认档案 (星黎下载 1)")
            self.assertEqual(Path(second["path"]).name, "星黎默认档案 (星黎下载 2)")
            self.assertEqual((existing / "modlist.txt").read_text(encoding="utf-8"), "+User Changes\n")

    def test_download_receipt_is_atomic_and_unseen_uses_semver_not_date(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "plugin-data" / "profile_download_receipts.json"
            old = {"id": "xingli-profile", "version": "1.0.0"}
            new = {"id": "xingli-profile", "version": "1.1.0"}
            same = {"id": "xingli-profile", "version": "1.0.0"}
            self.assertEqual(core.unseen([old], {}), [old])
            core.save_receipt(path, old)
            receipts = core.read_receipts(path)
            self.assertEqual(receipts["xingli-profile"], "1.0.0")
            self.assertEqual(core.unseen([same], receipts), [])
            self.assertEqual(core.unseen([new], receipts), [new])
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_invalid_old_receipt_does_not_break_notifications(self):
        record = {"id": "xingli-profile", "version": "1.0.0"}
        self.assertEqual(core.unseen([record], {"xingli-profile": "not-a-version"}), [record])


if __name__ == "__main__":
    unittest.main(verbosity=2)
