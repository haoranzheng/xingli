# coding: utf-8
"""No-network offline acceptance tests for per-MOD updates."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "xingli_mod_update_core", ROOT / "development/v2.4.0/mod_update_core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def archive_bytes():
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meshes/test.nif", b"mesh")
    return result.getvalue()


class Response:
    def __init__(self, blob, url, status=200, headers=None):
        self.buf = io.BytesIO(blob)
        self.url = url
        self.status = status
        self.headers = headers or {}

    def read(self, n=-1):
        return self.buf.read(n)

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class Mod:
    def __init__(self, path=None, version="1.0.0"):
        self.path = path
        self.v = version

    def version(self):
        return self.v

    def absolutePath(self):
        return str(self.path)

    def isSeparator(self):
        return False

    def isOverwrite(self):
        return False

    def isForeign(self):
        return False


class Organizer:
    def __init__(self, root, version="1.0.0"):
        self.root = root
        self.mod = Mod(root / "Installed Armor", version)

    def modList(self):
        return self

    def getMod(self, name):
        return self.mod if name == "Installed Armor" else None

    def modsPath(self):
        return str(self.root)


def sample(blob=None):
    payload = blob or archive_bytes()
    return {
        "schema": 1,
        "product": "xingli_mod_updates",
        "pack_id": "xingli-skyrim",
        "download_hosts": ["mods.example.com"],
        "mods": [{
            "id": "armor",
            "mo2_name": "Installed Armor",
            "version": "1.1.0",
            "changelog": "test only",
            "package": {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "mirrors": [{
                    "name": "R2",
                    "url": "https://mods.example.com/mods/armor/1.1.0.zip",
                }],
            },
        }],
    }


class Tests(unittest.TestCase):
    def test_catalog_accepts_author_domain(self):
        data = core.validate_catalog(sample(), core.DEFAULT_DOWNLOAD_HOSTS)
        self.assertEqual(data[0]["version"], "1.1.0")
        self.assertIn("mods.example.com", data[0]["_trusted_hosts"])

    def test_catalog_rejects_untrusted_download_host(self):
        catalog = sample()
        catalog["download_hosts"] = []
        with self.assertRaises(core.UpdateError):
            core.validate_catalog(catalog, core.DEFAULT_DOWNLOAD_HOSTS)

    def test_catalog_rejects_local_domains(self):
        for domain in ("localhost", "internal.local", "test.r2.dev"):
            catalog = sample()
            catalog["download_hosts"] = [domain]
            with self.assertRaises(core.UpdateError):
                core.validate_catalog(catalog, core.DEFAULT_DOWNLOAD_HOSTS)

    def test_catalog_rejects_checksum_and_duplicate_id(self):
        catalog = sample()
        catalog["mods"][0]["package"]["sha256"] = "0"
        with self.assertRaises(core.UpdateError):
            core.validate_catalog(catalog, core.DEFAULT_DOWNLOAD_HOSTS)
        catalog = sample()
        catalog["mods"].append(dict(catalog["mods"][0]))
        with self.assertRaises(core.UpdateError):
            core.validate_catalog(catalog, core.DEFAULT_DOWNLOAD_HOSTS)

    def test_update_matching_and_no_unknown_versions(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            organizer = Organizer(root)
            record = core.validate_catalog(sample(), core.DEFAULT_DOWNLOAD_HOSTS)[0]
            self.assertEqual(len(core.eligible_updates(organizer, [record])), 1)
            organizer.mod.v = "1.1.0"
            self.assertEqual(core.eligible_updates(organizer, [record]), [])
            organizer.mod.v = "unknown"
            self.assertEqual(core.eligible_updates(organizer, [record]), [])

    def test_mirror_download_resume_sha(self):
        blob = archive_bytes()
        record = core.validate_catalog(sample(blob), core.DEFAULT_DOWNLOAD_HOSTS)[0]
        url = record["package"]["mirrors"][0]["url"]
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d) / "armor"
            directory.mkdir()
            part = directory / "1.1.0.zip.part"
            part.write_bytes(blob[:13])
            (directory / "1.1.0.zip.part.json").write_text(json.dumps({
                "url": url, "sha256": record["package"]["sha256"]
            }), encoding="utf-8")

            def serve(request, timeout):
                self.assertEqual(request.headers["Range"], "bytes=13-")
                return Response(blob[13:], url, 206,
                                {"Content-Range": "bytes 13-%d/%d" %
                                 (len(blob) - 1, len(blob))})

            with patch.object(core.urllib.request, "urlopen", side_effect=serve):
                path = core.download_verified(record, d, core.DEFAULT_DOWNLOAD_HOSTS)
            self.assertEqual(Path(path).read_bytes(), blob)
            self.assertFalse(part.exists())

    def test_resume_ignored_range_restores_file_without_duplication(self):
        blob = archive_bytes()
        record = core.validate_catalog(sample(blob), core.DEFAULT_DOWNLOAD_HOSTS)[0]
        url = record["package"]["mirrors"][0]["url"]
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d) / "armor"
            directory.mkdir()
            (directory / "1.1.0.zip.part").write_bytes(blob[:13])
            (directory / "1.1.0.zip.part.json").write_text(json.dumps({
                "url": url, "sha256": record["package"]["sha256"]
            }), encoding="utf-8")
            with patch.object(core.urllib.request, "urlopen",
                              return_value=Response(blob, url, 200)):
                path = core.download_verified(record, d, core.DEFAULT_DOWNLOAD_HOSTS)
            self.assertEqual(Path(path).read_bytes(), blob)

    def test_sha_failure_cannot_be_installed(self):
        blob = archive_bytes()
        record = core.validate_catalog(sample(blob), core.DEFAULT_DOWNLOAD_HOSTS)[0]
        url = record["package"]["mirrors"][0]["url"]
        bad_blob = b"!" * len(blob)
        with tempfile.TemporaryDirectory() as d:
            with patch.object(core.urllib.request, "urlopen",
                              return_value=Response(bad_blob, url, 200)):
                with self.assertRaises(core.UpdateError):
                    core.download_verified(record, d, core.DEFAULT_DOWNLOAD_HOSTS)
            self.assertFalse((Path(d) / "armor/1.1.0.zip").exists())

    def test_backup_keeps_original_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "mods"
            folder = root / "Installed Armor"
            folder.mkdir(parents=True)
            (folder / "mesh.nif").write_text("A", encoding="utf-8")
            organizer = Organizer(root)
            destination = Path(d) / "backups"
            record = core.backup_mod(organizer, "Installed Armor", destination, "armor")
            backup = Path(record["backup_path"])
            self.assertEqual((backup / "mesh.nif").read_text(), "A")
            (folder / "mesh.nif").write_text("B", encoding="utf-8")
            self.assertEqual((backup / "mesh.nif").read_text(), "A")

    def test_catalog_from_repository_is_valid(self):
        data = json.loads((ROOT / "mods/latest.json").read_text(encoding="utf-8"))
        records = core.validate_catalog(data, core.DEFAULT_DOWNLOAD_HOSTS)
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
