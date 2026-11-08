# coding: utf-8
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).with_name("modpack_confirmation.py")
SPEC = importlib.util.spec_from_file_location("modpack_confirmation_tested", str(SOURCE))
confirmation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(confirmation)


class FakeOrganizer:
    def __init__(self, base, plugin_data):
        self._base = str(base)
        self._plugin_data = str(plugin_data)

    def basePath(self):
        return self._base

    def pluginDataPath(self):
        return self._plugin_data


class FakeController:
    def __init__(self, root):
        self.root = Path(root)
        self.plugin_path = str(self.root / "plugins" / "xingli_Little_assistant")
        self.plugin_data = self.root / "pluginData"
        self.base = self.root / "MO2"
        Path(self.plugin_path).mkdir(parents=True)
        self.plugin_data.mkdir(parents=True)
        self.base.mkdir(parents=True)
        self.organizer = FakeOrganizer(self.base, self.plugin_data)

    @property
    def persistent_path(self):
        return self.plugin_data / "xingli_assistant" / "modpack" / "installed_release.json"

    @property
    def root_marker_path(self):
        return self.base / "xingli_modpack_release.json"

    @property
    def bootstrap_path(self):
        return Path(self.plugin_path) / "modpack_release.json"

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class ModpackConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = FakeController(self.temp.name)
        self.bootstrap = {
            "schema": 2,
            "pack_id": "xingli-skyrim",
            "name": "星黎整合包",
            "installed_version": "3.3.1",
            "bootstrap_only": True,
        }
        self.controller.write_json(self.controller.bootstrap_path, self.bootstrap)
        self.current = {
            "pack_id": "xingli-skyrim",
            "name": "星黎整合包",
            "installed_version": "3.3.1",
        }
        self.manifest = {
            "schema": 2,
            "product": "xingli_modpack",
            "enabled": True,
            "pack_id": "xingli-skyrim",
            "name": "星黎整合包",
            "version": "4.0",
            "published_at": "2026-11-08T10:51:31Z",
        }

    def read_json(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def test_updates_authoritative_root_and_persistent_marker(self):
        self.controller.write_json(self.controller.root_marker_path, {
            "schema": 2, "pack_id": "xingli-skyrim",
            "name": "星黎整合包", "installed_version": "3.3.1",
            "root_custom_field": "preserve-root",
        })
        self.controller.write_json(self.controller.persistent_path, {
            "schema": 2, "pack_id": "xingli-skyrim",
            "installed_version": "3.3.1", "persistent_custom_field": "old",
        })

        result = confirmation.confirm_installed_version(
            self.controller, self.manifest, self.current)

        root = self.read_json(self.controller.root_marker_path)
        persistent = self.read_json(self.controller.persistent_path)
        self.assertEqual(result["installed_version"], "4.0")
        self.assertEqual(root["installed_version"], "4.0")
        self.assertEqual(persistent["installed_version"], "4.0")
        self.assertEqual(root["root_custom_field"], "preserve-root")
        self.assertEqual(persistent["root_custom_field"], "preserve-root")
        self.assertEqual(root["confirmation_source"], "user_confirmed_installed_release")
        self.assertTrue(root["confirmed_at_utc"])
        self.assertEqual(self.read_json(self.controller.bootstrap_path), self.bootstrap)

    def test_updates_persistent_marker_without_creating_root_marker(self):
        self.controller.write_json(self.controller.persistent_path, {
            "schema": 2, "pack_id": "xingli-skyrim",
            "name": "星黎整合包", "installed_version": "3.3.1",
            "custom_note": "keep",
        })

        result = confirmation.confirm_installed_version(
            self.controller, self.manifest, self.current)

        self.assertFalse(self.controller.root_marker_path.exists())
        persistent = self.read_json(self.controller.persistent_path)
        self.assertEqual(persistent["installed_version"], "4.0")
        self.assertEqual(persistent["custom_note"], "keep")
        self.assertEqual(result["marker_path"], str(self.controller.persistent_path))

    def test_seeds_persistent_marker_from_bootstrap_when_legacy_migration_missing(self):
        result = confirmation.confirm_installed_version(
            self.controller, self.manifest, self.current)
        persistent = self.read_json(self.controller.persistent_path)
        self.assertEqual(persistent["installed_version"], "4.0")
        self.assertTrue(persistent["bootstrap_only"])
        self.assertFalse(self.controller.root_marker_path.exists())
        self.assertEqual(result["installed_version"], "4.0")

    def test_rejects_wrong_pack_identity(self):
        manifest = dict(self.manifest, pack_id="other-pack")
        with self.assertRaisesRegex(ValueError, "标识不匹配"):
            confirmation.confirm_installed_version(self.controller, manifest, self.current)
        self.assertFalse(self.controller.persistent_path.exists())

    def test_rejects_wrong_product_or_disabled_manifest(self):
        with self.assertRaisesRegex(ValueError, "不是星黎整合包"):
            confirmation.confirm_installed_version(
                self.controller, dict(self.manifest, product="other"), self.current)
        with self.assertRaisesRegex(ValueError, "未启用"):
            confirmation.confirm_installed_version(
                self.controller, dict(self.manifest, enabled=False), self.current)
        self.assertFalse(self.controller.persistent_path.exists())

    def test_rejects_same_or_older_version(self):
        for version in ("3.3.1", "3.2.9", "2.0"):
            with self.subTest(version=version):
                with self.assertRaisesRegex(ValueError, "并未高于"):
                    confirmation.confirm_installed_version(
                        self.controller, dict(self.manifest, version=version), self.current)
        self.assertFalse(self.controller.persistent_path.exists())

    def test_rejects_invalid_versions(self):
        for version in ("", "latest", "4.0-beta", "../4.0"):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    confirmation.confirm_installed_version(
                        self.controller, dict(self.manifest, version=version), self.current)
        self.assertFalse(self.controller.persistent_path.exists())

    def test_does_not_overwrite_authoritative_root_when_root_write_fails(self):
        self.controller.write_json(self.controller.root_marker_path, {
            "schema": 2, "pack_id": "xingli-skyrim",
            "installed_version": "3.3.1",
        })
        self.controller.write_json(self.controller.persistent_path, {
            "schema": 2, "pack_id": "xingli-skyrim",
            "installed_version": "3.3.1",
        })
        with mock.patch.object(confirmation, "_write_json_atomic",
                               side_effect=OSError("simulated access denied")):
            with self.assertRaisesRegex(OSError, "access denied"):
                confirmation.confirm_installed_version(
                    self.controller, self.manifest, self.current)
        self.assertEqual(
            self.read_json(self.controller.root_marker_path)["installed_version"], "3.3.1")
        self.assertEqual(
            self.read_json(self.controller.persistent_path)["installed_version"], "3.3.1")

    def test_rejects_symlinked_active_marker(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        target = self.controller.base / "target.json"
        self.controller.write_json(target, {
            "schema": 2, "pack_id": "xingli-skyrim", "installed_version": "3.3.1"})
        try:
            self.controller.root_marker_path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            confirmation.confirm_installed_version(
                self.controller, self.manifest, self.current)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ModpackConfirmationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
